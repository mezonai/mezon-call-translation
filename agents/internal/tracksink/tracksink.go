// Package tracksink builds the two audiopipeline.Sink implementations
// cmd/agent wires into audiopipeline.Bridge for each newly-seen mic track:
// a record-service forwarder (RecordSinkFactory) and an STT client
// (STTSinkFactory). This lives in its own package -- separate from
// cmd/agent/main.go's session/wiring code -- because it is domain logic
// (mapping rtcagent.TrackInfo into record-service/STT request shapes,
// parsing STT results) rather than lifecycle/composition, and is testable
// in isolation from the rest of the agent's run loop.
package tracksink

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/audiopipeline"
	"github.com/mezonai/mezon-call-translation/agents/internal/config"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/orchestratorclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
	"github.com/mezonai/mezon-call-translation/agents/internal/sttclient"
)

// RecordSinkFactory builds a fresh record-service forwarder for each
// newly-seen track. audiopipeline.Bridge only learns a track's info
// (mid/user_id/peer_id/kind) when it first appears, so it has to call
// NewSink lazily per track rather than something built upfront -- that's
// the one piece of this that has to stay a func value handed to Bridge,
// not a nested closure.
type RecordSinkFactory struct {
	client *recordclient.Client
	cfg    config.Config
	roomID string
}

// NewRecordSinkFactory returns nil if recording is disabled for this run
// (recClient nil) -- callers must not wire a nil *RecordSinkFactory's
// NewSink method value into audiopipeline.Bridge, since that would panic on
// first use instead of no-oping. See cmd/agent's session.onJoined for the
// nil-guarded wiring this is meant to be used with.
//
// roomID must be orchestrator's own room UUID for this session (from
// orchestratorclient.Client.RegisterRoom, or its fallback if registration
// failed -- see cmd/agent's registerRoomWithOrchestrator), NOT mezon-sfu's
// numeric room id. record-service reports recording lifecycle events using
// this value verbatim, and orchestrator only resolves it back to a room if
// it's either that UUID or a room_name RegisterRoom populated its Redis
// fallback cache with -- see orchestratorclient.Client.RegisterRoom's doc.
func NewRecordSinkFactory(recClient *recordclient.Client, cfg config.Config, roomID string) *RecordSinkFactory {
	if recClient == nil {
		return nil
	}
	return &RecordSinkFactory{client: recClient, cfg: cfg, roomID: roomID}
}

func (f *RecordSinkFactory) NewSink(info rtcagent.TrackInfo) audiopipeline.Sink {
	fwd, err := recordclient.NewForwarder(f.client, recordclient.SessionMeta{
		RoomID: f.roomID,
		// mezon-sfu has no persistent track SID like LiveKit's
		// publication SID (mezon-sfu-migration-checklist.md A3.6) --
		// peer_id+kind is unique for the life of that remote peer's WS
		// session, as stable an identifier as the protocol offers.
		TrackID:             fmt.Sprintf("peer%d-%s", info.PeerID, info.Kind),
		ParticipantIdentity: strconv.FormatInt(info.UserID, 10),
		Source:              string(info.Kind), // always "mic": callers only ever see KindAudio tracks here, see rtcagent
		SampleRate:          audiopipeline.PCMSampleRate,
		Channels:            audiopipeline.PCMChannels,
	}, f.cfg.RecordService.MaxQueueSize)
	if err != nil {
		// Best-effort per audio-ingestion/PLAN.md D5 -- no recording for
		// this track must never be fatal to the call/STT.
		logging.L.Error("recordclient: failed to start forwarder", append(logging.ErrAttrs(err), "mid", info.Mid)...)
		return nil
	}
	return fwd
}

// STTSinkFactory builds a fresh STT client for each newly-seen track (same
// lazy-per-track reasoning as RecordSinkFactory).
type STTSinkFactory struct {
	cfg      config.Config
	orch     *orchestratorclient.Client
	roomName string // see orchestratorclient's package doc re: room_name vs room_id
}

// NewSTTSinkFactory returns nil if STT can't do anything useful this run
// (no STT host configured, or no orchestrator to push transcripts to and no
// way it could ever be enabled -- see cmd/agent's registerRequestHandlers,
// which only registers transcript_control when orch is non-nil). Same
// nil-guarded-wiring caveat as NewRecordSinkFactory applies.
func NewSTTSinkFactory(cfg config.Config, orch *orchestratorclient.Client) *STTSinkFactory {
	if cfg.STT.Host == "" || orch == nil {
		return nil
	}
	return &STTSinkFactory{cfg: cfg, orch: orch, roomName: strconv.FormatUint(cfg.RoomID, 10)}
}

func (f *STTSinkFactory) NewSink(info rtcagent.TrackInfo) audiopipeline.Sink {
	clientID := fmt.Sprintf("peer%d-%s", info.PeerID, info.Kind)
	participantIdentity := strconv.FormatInt(info.UserID, 10)
	wsURL := fmt.Sprintf("ws://%s:%d/ws/vosk/?client_id=%s&session_id=%s",
		f.cfg.STT.Host, f.cfg.STT.Port, url.QueryEscape(clientID), url.QueryEscape(f.roomName))

	fwd := &transcriptForwarder{orch: f.orch, roomName: f.roomName, participantIdentity: participantIdentity}
	c, err := sttclient.Dial(context.Background(), wsURL, f.cfg.STT.MaxQueueSize, fwd.onMessage)
	if err != nil {
		logging.L.Error("sttclient: failed to dial", append(logging.ErrAttrs(err), "mid", info.Mid)...)
		return nil
	}
	return c
}

// transcriptForwarder relays one track's parsed STT results to the
// orchestrator (participantIdentity is fixed at construction, one instance
// per track). Its onMessage method is handed to sttclient.Dial as the
// message callback -- sttclient's read loop is inherently event-driven
// (same class of boundary as rtcagent/signaling, unavoidable), but keeping
// the per-track state on a struct instead of closure-captured locals means
// onMessage/push read like normal methods instead of a closure nested
// inside a closure.
type transcriptForwarder struct {
	orch                *orchestratorclient.Client
	roomName            string
	participantIdentity string
}

func (t *transcriptForwarder) onMessage(raw []byte) {
	text, isFinal, ok := parseTranscriptMessage(raw)
	if !ok || text == "" {
		return
	}
	messageType := "PARTIAL"
	if isFinal {
		messageType = "FINAL"
	}
	// Fire-and-forget, off the STT client's own read-loop goroutine -- pushing
	// to orchestrator must never delay reading the next transcription result.
	go t.push(text, messageType)
}

// pushTranscriptTimeout: small on purpose, guards against orchestrator/
// network slowness, not genuinely necessary work -- see orchestratorCallTimeout
// in cmd/agent/main.go for the same reasoning applied elsewhere.
const pushTranscriptTimeout = 3 * time.Second

func (t *transcriptForwarder) push(text, messageType string) {
	pctx, cancel := context.WithTimeout(context.Background(), pushTranscriptTimeout)
	defer cancel()
	if err := t.orch.PushTranscript(pctx, t.roomName, text, t.participantIdentity, messageType); err != nil {
		logging.L.Warn("orchestratorclient: push_transcript failed", logging.ErrAttrs(err)...)
	}
}

// parseTranscriptMessage mirrors the old Python agent's parsing in
// event_handlers.py's transcription_callback: JSON {"text","is_final"} if
// the message looks like JSON, otherwise the raw message is the text
// (is_final defaults to false in that case).
func parseTranscriptMessage(raw []byte) (text string, isFinal bool, ok bool) {
	s := strings.TrimSpace(string(raw))
	if s == "" {
		return "", false, false
	}
	if strings.HasPrefix(s, "{") {
		var data struct {
			Text    string `json:"text"`
			IsFinal bool   `json:"is_final"`
		}
		if err := json.Unmarshal([]byte(s), &data); err != nil {
			return "", false, false
		}
		return strings.TrimSpace(data.Text), data.IsFinal, true
	}
	return s, false, true
}
