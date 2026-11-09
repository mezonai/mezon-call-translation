// Package tracksink builds the record-service audiopipeline.Sink that
// cmd/agent wires into audiopipeline.Bridge for each newly-seen mic track.
// This lives in its own package -- separate from
// cmd/agent/main.go's session/wiring code -- because it is domain logic
// (mapping rtcagent.TrackInfo into record-service/STT request shapes,
// parsing STT results) rather than lifecycle/composition, and is testable
// in isolation from the rest of the agent's run loop.
package tracksink

import (
	"fmt"
	"strconv"

	"github.com/mezonai/mezon-call-translation/agents/internal/audiopipeline"
	"github.com/mezonai/mezon-call-translation/agents/internal/config"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
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
	conn   int
	epoch  int
}

// RecordTrackID is unique per (peer, kind, signaling connection). The
// connection ordinal keeps a reconnect from reusing the previous
// connection's record-service session / orchestrator tracks row, which are
// keyed by room_id:track_id and stay the same across reconnects.
func RecordTrackID(peerID uint64, kind rtcagent.TrackKind, conn int, epoch ...int) string {
	id := fmt.Sprintf("peer%d-%s-c%d", peerID, kind, conn)
	// Preserve existing IDs for the first joined. An in-place rejoin rebuilds
	// sinks on the same socket and must not overwrite that earlier recording.
	if len(epoch) > 0 && epoch[0] > 1 {
		id += fmt.Sprintf("-e%d", epoch[0])
	}
	return id
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
func NewRecordSinkFactory(recClient *recordclient.Client, cfg config.Config, roomID string, conn int, epoch ...int) *RecordSinkFactory {
	if recClient == nil {
		return nil
	}
	f := &RecordSinkFactory{client: recClient, cfg: cfg, roomID: roomID, conn: conn}
	if len(epoch) > 0 {
		f.epoch = epoch[0]
	}
	return f
}

func (f *RecordSinkFactory) NewSink(info rtcagent.TrackInfo) audiopipeline.Sink {
	fwd, err := recordclient.NewForwarder(f.client, recordclient.SessionMeta{
		RoomID: f.roomID,
		// mezon-sfu has no persistent track SID like LiveKit's
		// publication SID (mezon-sfu-migration-checklist.md A3.6) --
		// peer_id+kind is unique for the life of that remote peer's WS
		// session, as stable an identifier as the protocol offers.
		TrackID:             RecordTrackID(info.PeerID, info.Kind, f.conn, f.epoch),
		ParticipantIdentity: strconv.FormatInt(info.UserID, 10),
		Source:              string(info.Kind), // always "mic": callers only ever see KindAudio tracks here, see rtcagent
		SampleRate:          audiopipeline.PCMSampleRate,
		Channels:            audiopipeline.PCMChannels,
	}, f.cfg.RecordService.MaxQueueSize)
	if err != nil {
		// Best-effort per audio-ingestion/PLAN.md D5 -- no recording for
		// this track must never be fatal to the call.
		logging.L.Error("recordclient: failed to start forwarder", append(logging.ErrAttrs(err), "mid", info.Mid)...)
		return nil
	}
	return fwd
}
