// Package ttsplayer publishes synthesized speech into a mezon-sfu room.
// Ported from the old Python agent's TTSManager
// (agents/src/core/tts_manager.py), narrowed to what
// mezon-sfu-migration-plan.md 2.5 actually scopes: synthesize -> Opus
// encode -> publish via a pion TrackLocalStaticSample, plus (best-effort)
// forwarding the same audio to record-service so the bot's own speech is
// captured too, and reporting each utterance as a transcript segment / a
// "TTS completed" marker to orchestrator's /api/v2/recordings/events
// (replaces a Whisper job for this track -- see orchestratorclient's
// ReportTTSTranscript/ReportTTSCompleted docs for why the latter is not
// optional).
//
// NOT ported (interview-flow-specific, out of scope for this pass):
//   - the silence-gap ticker that pads record-service's timeline through
//     long silent gaps between utterances so the recorded file's duration
//     tracks wall-clock time;
//   - TTS status push-back to room participants (was LiveKit DataChannel
//     `tts_status` -- mezon-sfu has no data channel yet).
package ttsplayer

import (
	"context"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
	"github.com/mezonai/mezon-call-translation/agents/internal/orchestratorclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsclient"
)

const (
	frameDuration     = 20 * time.Millisecond
	speakQueueSize    = 8
	synthesizeTimeout = 30 * time.Second

	// reportTimeout/closeGrace: intentionally small -- these guard against
	// network/orchestrator slowness, not against genuinely necessary work
	// (see mezon-sfu-migration-checklist.md D4's shutdown-timing discussion).
	// A call that would succeed in 5s but not 3s isn't worth optimizing
	// for; a call to a genuinely down orchestrator won't succeed no matter
	// how long we wait, so failing fast costs nothing and bounds shutdown
	// latency instead.
	reportTimeout = 3 * time.Second
	closeGrace    = 3 * time.Second
)

type speakRequest struct {
	text, voice string
	speed       float64
}

// Player owns one publish track for the lifetime of one mezon-sfu session
// (like rtcagent.PeerAgent, not reused across reconnects).
type Player struct {
	track      *webrtc.TrackLocalStaticSample
	tts        *ttsclient.Client
	encoder    opusenc.Encoder
	sampleRate int

	recClient    *recordclient.Client
	roomID       string
	trackID      string
	participant  string
	maxQueueSize int

	orch *orchestratorclient.Client // may be nil: no transcript/completed reporting then, see New's doc

	mu                sync.Mutex
	forwarder         *recordclient.Forwarder // lazily created on first utterance, lives for the Player's whole life
	sessionStartEpoch time.Time               // set alongside forwarder; report_tts_transcript's start/end are relative to this

	requests chan speakRequest
	done     chan struct{}

	// stopCtx/stopCancel: cancelled by Close so an in-flight speakNow
	// (specifically its Synthesize HTTP call, see stopCancel's use in
	// processQueue) unwinds immediately instead of running to its own
	// synthesizeTimeout -- see Close's doc for why this matters for
	// shutdown latency.
	stopCtx    context.Context
	stopCancel context.CancelFunc
}

// New builds a Player. track must already be attached to the
// PeerConnection (rtcagent.New's publishTrack parameter, role "speaker")
// before the first offer is answered. recClient may be nil (recording
// disabled/unavailable), matching internal/audiopipeline's convention.
//
// Returns an error if opusenc.New fails (see that package's doc for what
// can make that happen) -- callers should treat that as "TTS unavailable
// this session", not fatal to joining the room as a speaker (attaching the
// publish track itself doesn't need a working encoder, only Speak does).
//
// roomID must be orchestrator's own room UUID for this session (from
// orchestratorclient.Client.RegisterRoom, or its fallback -- see cmd/agent's
// registerRoomWithOrchestrator), not mezon-sfu's numeric room id -- this
// forwards the agent's own TTS audio to record-service the same way
// internal/tracksink.RecordSinkFactory does for remote tracks, and needs
// the same room_id record-service/orchestrator can actually resolve. See
// orchestratorclient.Client.RegisterRoom's doc for why.
//
// orch may be nil (no ORCHESTRATOR_BASE_URL configured), in which case
// per-utterance transcript / completion reporting is silently skipped --
// callers that do have an orch client should always pass it, since skipping
// ReportTTSCompleted stalls this room's summary forever, see that method's
// doc.
func New(
	track *webrtc.TrackLocalStaticSample,
	ttsClient *ttsclient.Client,
	sampleRate int,
	recClient *recordclient.Client,
	roomID string,
	agentUserID int64,
	maxQueueSize int,
	orch *orchestratorclient.Client,
) (*Player, error) {
	enc, err := opusenc.New(sampleRate, 1)
	if err != nil {
		return nil, fmt.Errorf("ttsplayer: %w", err)
	}

	stopCtx, stopCancel := context.WithCancel(context.Background())
	p := &Player{
		track:        track,
		tts:          ttsClient,
		encoder:      enc,
		sampleRate:   sampleRate,
		recClient:    recClient,
		roomID:       roomID,
		trackID:      "agent-tts",
		participant:  strconv.FormatInt(agentUserID, 10),
		maxQueueSize: maxQueueSize,
		orch:         orch,
		requests:     make(chan speakRequest, speakQueueSize),
		done:         make(chan struct{}),
		stopCtx:      stopCtx,
		stopCancel:   stopCancel,
	}
	go p.processQueue()
	return p, nil
}

// Speak queues one utterance for playback. Non-blocking, and safe to call
// from the SSE request-handling goroutine (internal/orchestratorclient)
// directly: a background worker plays queued utterances one at a time
// (mirrors the old Python TTSManager's request queue -- "only one audio
// stream active at a time"), so Speak never blocks on synthesis or on a
// previous utterance still playing. If the queue is full, the request is
// dropped and logged rather than blocking or growing unbounded.
func (p *Player) Speak(text, voice string, speed float64) {
	select {
	case p.requests <- speakRequest{text: text, voice: voice, speed: speed}:
	default:
		logging.L.Warn("ttsplayer: request queue full, dropping tts_play request", "text_preview", previewText(text))
	}
}

func (p *Player) processQueue() {
	defer close(p.done)
	for req := range p.requests {
		select {
		case <-p.stopCtx.Done():
			// Close was called: drop whatever's still queued rather than
			// playing out a backlog during shutdown -- see Close's doc.
			continue
		default:
		}
		// Derived from stopCtx, not context.Background(): Close cancelling
		// stopCtx aborts an in-flight Synthesize call (net/http respects
		// context cancellation) instead of letting it run the full
		// synthesizeTimeout.
		ctx, cancel := context.WithTimeout(p.stopCtx, synthesizeTimeout)
		err := p.speakNow(ctx, req.text, req.voice, req.speed)
		cancel()
		if err != nil {
			logging.L.Error("ttsplayer: failed to speak", append(logging.ErrAttrs(err), "text_preview", previewText(req.text))...)
		}
	}
}

func (p *Player) speakNow(ctx context.Context, text, voice string, speed float64) error {
	pcm, err := p.tts.Synthesize(ctx, text, voice, speed)
	if err != nil {
		return fmt.Errorf("synthesize: %w", err)
	}

	frameSamples := p.sampleRate / 50 // 20ms
	if frameSamples <= 0 {
		return fmt.Errorf("invalid sample rate %d", p.sampleRate)
	}

	// utteranceStart: captured right before the real frames for this
	// utterance start going out, matching the old Python TTSManager's
	// _record_utterance_start_epoch (used, not request_start, so synthesis
	// latency above doesn't leak into the reported segment timing).
	utteranceStart := time.Now()
	samplesWritten := 0
	for off := 0; off+frameSamples <= len(pcm); off += frameSamples {
		frame := pcm[off : off+frameSamples]

		opusPayload, err := p.encoder.Encode(frame)
		if err != nil {
			return fmt.Errorf("opus encode: %w", err)
		}
		if err := p.track.WriteSample(media.Sample{Data: opusPayload, Duration: frameDuration}); err != nil {
			return fmt.Errorf("write sample: %w", err)
		}
		p.forwardToRecordService(int16ToLEBytes(frame))
		samplesWritten += len(frame)
	}
	// Fresh context, not the (possibly just-cancelled-by-Close) ctx above:
	// this report matters even when the utterance finished writing frames
	// right as Close() cancelled stopCtx -- see reportTTSTranscript's doc.
	rctx, cancel := context.WithTimeout(context.Background(), reportTimeout)
	defer cancel()
	p.reportTTSTranscript(rctx, text, utteranceStart, float64(samplesWritten)/float64(p.sampleRate))
	return nil
}

// reportTTSTranscript posts this utterance as a transcript segment for the
// agent's own TTS track (orchestratorclient.Client.ReportTTSTranscript) --
// best-effort, mirrors the old Python TTSManager's own posture ("an
// orchestrator hiccup here must never fail TTS playback itself"). Skipped
// entirely if no forwarder was ever started this session (recording
// disabled/failed, or orch not configured) -- matches the old Python
// client's own guard, since without a forwarder there was never a
// recording.started event to create the Track row this appends to.
func (p *Player) reportTTSTranscript(ctx context.Context, text string, utteranceStart time.Time, audioDuration float64) {
	if p.orch == nil {
		return
	}
	p.mu.Lock()
	sessionStart := p.sessionStartEpoch
	hasForwarder := p.forwarder != nil
	p.mu.Unlock()
	if !hasForwarder || sessionStart.IsZero() {
		return
	}

	start := utteranceStart.Sub(sessionStart).Seconds()
	if err := p.orch.ReportTTSTranscript(ctx, p.roomID, p.trackID, text, start, start+audioDuration); err != nil {
		logging.L.Warn("orchestratorclient: report_tts_transcript failed", logging.ErrAttrs(err)...)
	}
}

// forwardToRecordService lazily starts the forwarder on first use.
// Guarded by p.mu because, unlike internal/audiopipeline's per-track
// sessions (one goroutine owns a forwarder for its whole life), this
// forwarder is touched by both processQueue's single worker goroutine and
// Close (called from session teardown, a different goroutine) -- real
// mutual exclusion, not just goroutine affinity, matters here.
func (p *Player) forwardToRecordService(pcm []byte) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.recClient == nil {
		return
	}
	if p.forwarder == nil {
		fwd, err := recordclient.NewForwarder(p.recClient, recordclient.SessionMeta{
			RoomID:              p.roomID,
			TrackID:             p.trackID,
			ParticipantIdentity: p.participant,
			Source:              "mic",
			SampleRate:          int32(p.sampleRate),
			Channels:            1,
		}, p.maxQueueSize)
		if err != nil {
			logging.L.Error("ttsplayer: failed to start record-service forwarder", logging.ErrAttrs(err)...)
			p.recClient = nil // best-effort per PLAN.md D5: don't retry every frame after one failure
			return
		}
		p.forwarder = fwd
		p.sessionStartEpoch = time.Now()
	}
	p.forwarder.SendPCM(pcm)
}

// Close cancels any in-flight utterance (aborting its Synthesize call
// immediately rather than letting it run out synthesizeTimeout), drops
// whatever's still queued behind it, closes the record-service forwarder if
// one was started, and reports the track as done to orchestrator (see
// ReportTTSCompleted's doc for why that call matters). closeGrace is a
// safety net, not the expected path -- stopCancel should make processQueue
// exit almost immediately.
func (p *Player) Close() {
	p.stopCancel()
	close(p.requests)
	select {
	case <-p.done:
	case <-time.After(closeGrace):
	}

	p.mu.Lock()
	fwd := p.forwarder
	p.forwarder = nil
	hadForwarder := fwd != nil
	p.mu.Unlock()
	if fwd != nil {
		fwd.Close()
	}

	// Only if a forwarder was ever actually started this session -- no
	// recording.started event ever fired otherwise, so there is no Track
	// row for this to mark complete (mirrors the old Python TTSManager's
	// same guard in its cleanup()).
	if hadForwarder && p.orch != nil {
		ctx, cancel := context.WithTimeout(context.Background(), reportTimeout)
		defer cancel()
		if err := p.orch.ReportTTSCompleted(ctx, p.roomID, p.trackID); err != nil {
			logging.L.Error("orchestratorclient: report_tts_completed failed -- this room's summary may get stuck",
				logging.ErrAttrs(err)...)
		}
	}
}

func previewText(text string) string {
	const maxLen = 50
	if len(text) <= maxLen {
		return text
	}
	return text[:maxLen] + "..."
}

func int16ToLEBytes(samples []int16) []byte {
	out := make([]byte, len(samples)*2)
	for i, v := range samples {
		out[i*2] = byte(v)
		out[i*2+1] = byte(v >> 8)
	}
	return out
}
