// Command agent is the per-room mezon-sfu WebRTC client. It is spawned as a
// subprocess by the orchestrator's worker manager (mezon-sfu-migration-plan.md
// section 1) and does not know or care what triggered the spawn -- it only
// reads room_id/role/jwt_secret/user_id from the environment (internal/config),
// joins the room, and keeps the session alive until the process is killed or
// the WS session dies.
package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/joho/godotenv"
	"github.com/pion/webrtc/v4"

	"github.com/mezonai/mezon-call-translation/agents/internal/audiopipeline"
	"github.com/mezonai/mezon-call-translation/agents/internal/config"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/orchestratorclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/reconnect"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
	"github.com/mezonai/mezon-call-translation/agents/internal/sfuauth"
	"github.com/mezonai/mezon-call-translation/agents/internal/signaling"
	"github.com/mezonai/mezon-call-translation/agents/internal/tracksink"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsplayer"
)

// orchestratorCallTimeout bounds the register/unregister-room HTTP calls.
// Small on purpose: guards against orchestrator/network being slow, not
// genuinely necessary work -- a call that would succeed in 5s but not 3s
// isn't worth optimizing for, and one to a genuinely down orchestrator won't
// succeed no matter how long we wait (both calls already degrade gracefully
// on failure rather than blocking startup/shutdown, see their call sites).
const orchestratorCallTimeout = 3 * time.Second

func main() {
	// Best-effort: only matters when bin/agent is run standalone for
	// debugging (bypassing worker-manager) -- worker-manager already loads
	// its own .env and passes the relevant vars through explicitly
	// (internal/workermanager/config.go's agentPassthroughEnvKeys), so this
	// is a no-op duplicate in that path, not a conflicting second source.
	_ = godotenv.Load()

	cfg, err := config.FromEnv()
	if err != nil {
		logging.L.Error("config: failed to load", logging.ErrAttrs(err)...)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Recording is best-effort (audio-ingestion/PLAN.md D5): a dial failure
	// here must not stop the agent from joining the room, just disable
	// forwarding for this run.
	recClient, recErr := recordclient.Dial(cfg.RecordService.GRPCAddr)
	if recErr != nil {
		logging.L.Error("recordclient: failed to dial record-service, recording disabled for this run",
			append(logging.ErrAttrs(recErr), "grpc_addr", cfg.RecordService.GRPCAddr)...)
		recClient = nil
	} else {
		defer func() { _ = recClient.Close() }()
	}

	// refs holds the *current* session's bridge/player -- both are rebuilt
	// on every reconnect (see runSession), but the orchestrator SSE
	// listener below is started once and outlives every individual
	// mezon-sfu session, so its handlers need a way to reach "whatever
	// session is live right now" (or no-op if none is, e.g. mid-reconnect).
	refs := &sessionRefs{}

	var orch *orchestratorclient.Client
	if cfg.Orchestrator.BaseURL != "" {
		orch = orchestratorclient.New(cfg.Orchestrator.BaseURL, cfg.Orchestrator.APIKey)
		registerRequestHandlers(orch, refs)

		go func() {
			agentID := strconv.FormatInt(cfg.AgentUserID, 10)
			// room_name: see internal/orchestratorclient's package doc --
			// this is room_id as a decimal string, an assumption pending
			// confirmation from the orchestrator side, not a settled contract.
			roomName := strconv.FormatUint(cfg.RoomID, 10)
			if err := orch.RunAgentRequestListener(ctx, agentID, roomName); err != nil && ctx.Err() == nil {
				logging.L.Error("orchestratorclient: sse listener exited unexpectedly", logging.ErrAttrs(err)...)
			}
		}()
	} else {
		logging.L.Info("orchestratorclient: ORCHESTRATOR_BASE_URL not set, running without TTS trigger/transcript push/transcript_control")
	}

	err = run(ctx, cfg, recClient, orch, refs)
	switch {
	case err == nil:
		return
	case errors.Is(err, context.Canceled):
		// Graceful shutdown (SIGINT/SIGTERM, e.g. worker manager's Stop()) --
		// not a failure. Exiting 0 here matters: workermanager.reap logs the
		// exit and this is what lets it (and anything else watching this
		// process) tell "asked to leave" apart from "gave up"/crashed.
		logging.L.Info("agent: stopped", "reason", "context canceled")
	default:
		logging.L.Error("agent: exiting", logging.ErrAttrs(err)...)
		os.Exit(1)
	}
}

// sessionRefs is how the long-lived orchestrator SSE listener's handlers
// reach the current mezon-sfu session's audiopipeline.Bridge/ttsplayer.Player
// -- both nil between sessions (mid-reconnect) or if the corresponding
// feature isn't active this run (e.g. no Player unless Role is "speaker").
type sessionRefs struct {
	mu     sync.Mutex
	bridge *audiopipeline.Bridge
	player *ttsplayer.Player
}

func (r *sessionRefs) set(bridge *audiopipeline.Bridge, player *ttsplayer.Player) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.bridge, r.player = bridge, player
}

func (r *sessionRefs) get() (*audiopipeline.Bridge, *ttsplayer.Player) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.bridge, r.player
}

// registerRequestHandlers wires the two orchestrator SSE agent-request
// types this pass ports (see orchestratorclient's package doc for what's
// deliberately not ported): tts_play drives ttsplayer.Player.Speak,
// transcript_control drives audiopipeline.Bridge.SetSTTEnabled.
func registerRequestHandlers(orch *orchestratorclient.Client, refs *sessionRefs) {
	orch.RegisterHandler("tts_play", func(payload map[string]any) error {
		_, player := refs.get()
		if player == nil {
			return fmt.Errorf("no active speaker-role session to play TTS into")
		}
		text, _ := payload["text"].(string)
		if strings.TrimSpace(text) == "" {
			return fmt.Errorf("missing/empty text")
		}
		voice, _ := payload["voice"].(string)
		speed, _ := payload["speed"].(float64)
		player.Speak(text, voice, speed)
		return nil
	})

	orch.RegisterHandler("transcript_control", func(payload map[string]any) error {
		bridge, _ := refs.get()
		if bridge == nil {
			return fmt.Errorf("no active session")
		}
		action, _ := payload["action"].(string)
		switch action {
		case "enable":
			bridge.SetSTTEnabled(true)
		case "disable":
			bridge.SetSTTEnabled(false)
		default:
			return fmt.Errorf("unknown action %q (want \"enable\" or \"disable\")", action)
		}
		return nil
	})
}

// run keeps a mezon-sfu session alive, retrying with backoff after a
// session dies (see config.ReconnectConfig for why this is a full rejoin,
// not a resume, and what it is not a substitute for). It only returns once
// ctx is cancelled (graceful shutdown, e.g. worker manager sent SIGTERM --
// no point retrying that) or the retry budget is exhausted.
func run(ctx context.Context, cfg config.Config, recClient *recordclient.Client, orch *orchestratorclient.Client, refs *sessionRefs) error {
	backoff := reconnect.New(cfg.Reconnect)

	for {
		start := time.Now()
		err := runSession(ctx, cfg, recClient, orch, refs)

		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err == nil {
			// runSession blocks until error or ctx cancellation; a nil
			// return with ctx still live shouldn't happen, but treat it as
			// a terminal success rather than looping forever.
			return nil
		}

		if time.Since(start) >= cfg.Reconnect.StableAfter {
			backoff.Reset()
		}

		delay, ok := backoff.Next()
		if !ok {
			return fmt.Errorf("agent: giving up after %d reconnect attempt(s), last error: %w", backoff.Attempts(), err)
		}

		logging.L.Warn("agent: session ended, will reconnect",
			append(logging.ErrAttrs(err), "attempt", backoff.Attempts(), "max_attempts", cfg.Reconnect.MaxAttempts, "retry_in", delay)...)

		select {
		case <-time.After(delay):
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

// runSession performs one full join: sign a fresh JWT, dial, complete the
// handshake, and block until the session ends (WS error/close) or ctx is
// cancelled. Every call builds a brand new session -- mezon-sfu ties the
// media session to the WS connection 1:1, so there is nothing to resume
// from a previous attempt, and mid numbering restarts from scratch too.
func runSession(ctx context.Context, cfg config.Config, recClient *recordclient.Client, orch *orchestratorclient.Client, refs *sessionRefs) error {
	token, err := sfuauth.SignJoinToken(cfg.JWTSecret, cfg.AgentUserID, cfg.RoomID, cfg.TokenTTL)
	if err != nil {
		return err
	}

	// roomName: see orchestratorclient's package doc -- mezon-sfu's own
	// numeric room_id, used as room_name everywhere the old contract wanted
	// one. roomID: orchestrator's own UUID for this session, minted and
	// registered below (or roomName itself as a degrade fallback) -- these
	// are two different identifiers, do not conflate them.
	roomName := strconv.FormatUint(cfg.RoomID, 10)
	roomID := registerRoomWithOrchestrator(ctx, orch, roomName)

	sess := newSession(cfg, recClient, orch, refs, roomName, roomID)

	client, err := signaling.Dial(ctx, cfg.SFUWebSocketURL, token, string(cfg.Role), sess.callbacks())
	if err != nil {
		return err
	}
	defer func() {
		sess.close()
		_ = client.Close()
	}()

	logging.L.Info("agent: dialed mezon-sfu, awaiting handshake",
		"ws_url", cfg.SFUWebSocketURL, "room_id", cfg.RoomID, "orchestrator_room_id", roomID, "role", cfg.Role, "agent_user_id", cfg.AgentUserID)

	return client.Run(ctx)
}

// registerRoomWithOrchestrator mints a fresh UUID and registers it with
// orchestrator as this session's stable room_id (see
// orchestratorclient.Client.RegisterRoom's doc for why this matters -- it's
// what lets record-service's recording events for this session resolve to
// a real room instead of getting silently dropped). Mirrors the old Python
// agent's main.go: called before dialing mezon-sfu, so room_id is already
// in hand before any track can possibly start recording.
//
// orch nil (ORCHESTRATOR_BASE_URL unset) or a failed/unreachable register
// call both degrade to using roomName itself as the room_id, matching the
// old Python agent's fallback ("room_id stays None, downstream code falls
// back to ctx.room.name") -- recording still proceeds, just without a shot
// at resolving through orchestrator's registry either, same as before.
func registerRoomWithOrchestrator(ctx context.Context, orch *orchestratorclient.Client, roomName string) string {
	if orch == nil {
		return roomName
	}
	agentRoomID := uuid.NewString()
	rctx, cancel := context.WithTimeout(ctx, orchestratorCallTimeout)
	defer cancel()
	if err := orch.RegisterRoom(rctx, roomName, agentRoomID); err != nil {
		logging.L.Warn("orchestratorclient: register_room failed, recording events for this session may not resolve",
			append(logging.ErrAttrs(err), "room_name", roomName)...)
		return roomName
	}
	return agentRoomID
}

// session holds the mutable state of a single mezon-sfu join --
// rtcagent.PeerAgent and (if speaking) ttsplayer.Player -- and is the
// receiver for every signaling.Callbacks hook. Bundling these as fields
// instead of closure-captured locals means the state each handler reads is
// explicit and the handlers are independently readable/testable, rather
// than a block of nested funcs all reaching into the same captured
// variables.
//
// No locking needed: every signaling.Callbacks hook runs sequentially on
// signaling.Client's single read-loop goroutine, and onJoined always fires
// before onOffer per the join handshake order
// (mezon-sfu-migration-checklist.md C.2).
type session struct {
	cfg       config.Config
	recClient *recordclient.Client
	orch      *orchestratorclient.Client
	refs      *sessionRefs
	// roomName/roomID: see runSession's comment at the call site and
	// orchestratorclient's package doc -- two different identifiers, roomID
	// is what record-service/TTS forwarding need, roomName is what
	// push_transcript/SSE/STT use.
	roomName string
	roomID   string

	peerAgent *rtcagent.PeerAgent
	player    *ttsplayer.Player
}

func newSession(cfg config.Config, recClient *recordclient.Client, orch *orchestratorclient.Client, refs *sessionRefs, roomName, roomID string) *session {
	return &session{cfg: cfg, recClient: recClient, orch: orch, refs: refs, roomName: roomName, roomID: roomID}
}

func (s *session) callbacks() signaling.Callbacks {
	return signaling.Callbacks{
		OnJoined:       s.onJoined,
		OnOffer:        s.onOffer,
		OnRoomSnapshot: s.onRoomSnapshot,
		OnPeerJoined:   s.onPeerJoined,
		OnPeerLeft:     s.onPeerLeft,
		OnPeerUpdated:  s.onPeerUpdated,
	}
}

func (s *session) onJoined(room uint64, iceServers []signaling.ICEServer) {
	logging.L.Info("signaling: joined", "room", room, "ice_servers", len(iceServers))

	var publishTrack *webrtc.TrackLocalStaticSample
	if s.cfg.Role == config.RoleSpeaker {
		// Channels must be 2 here per RFC 7587 (Opus RTP payload format):
		// the SDP rtpmap always declares 2 channels regardless of actual
		// mono/stereo content -- mezon-sfu's offer says "opus/48000/2"
		// (sdp.c) and so does pion's own default Opus registration
		// (mediaengine.go). Actual mono output is unaffected: that's
		// controlled by the (absent, so default) fmtp "stereo" param, not
		// this field. Declaring Channels:1 here made pion's codec fuzzy
		// match (internal/fmtp.ChannelsEqual) fail against the negotiated
		// "opus/48000/2" codec -- Bind() would return ErrUnsupportedCodec
		// even with a working encoder.
		track, err := webrtc.NewTrackLocalStaticSample(
			webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2},
			"tts-audio", "agent-tts",
		)
		if err != nil {
			logging.L.Error("ttsplayer: failed to create publish track, joining as audience instead", logging.ErrAttrs(err)...)
		} else {
			publishTrack = track
		}
	}

	// Classic Go typed-nil-interface trap: rtcagent.New takes webrtc.TrackLocal
	// (an interface), and publishTrack's static type is the concrete
	// *webrtc.TrackLocalStaticSample -- passing a nil publishTrack straight
	// through would implicitly wrap it into a non-nil interface (type set,
	// value nil), so rtcagent.New's own `!= nil` check would see it as
	// non-nil and call pc.AddTrack on a nil pointer (confirmed: panics
	// inside pion's AddTrack -> TrackLocalStaticSample.Kind, nil receiver).
	// Converting explicitly here, while publishTrack is still the concrete
	// type, keeps the interface genuinely nil for the audience case.
	var rtcPublishTrack webrtc.TrackLocal
	if publishTrack != nil {
		rtcPublishTrack = publishTrack
	}
	pa, err := rtcagent.New(iceServers, rtcPublishTrack)
	if err != nil {
		logging.L.Error("rtcagent: failed to create peer connection", logging.ErrAttrs(err)...)
		return
	}

	// recordFactory/sttFactory are nil when that consumer is disabled for
	// this run (see tracksink's constructors); newRecordSink/newSTTSink must
	// stay nil funcs (not a method value bound to a nil receiver) in that
	// case -- Bridge nil-checks the func itself before calling it.
	recordFactory := tracksink.NewRecordSinkFactory(s.recClient, s.cfg, s.roomID)
	sttFactory := tracksink.NewSTTSinkFactory(s.cfg, s.orch)
	var newRecordSink, newSTTSink func(info rtcagent.TrackInfo) audiopipeline.Sink
	if recordFactory != nil {
		newRecordSink = recordFactory.NewSink
	}
	if sttFactory != nil {
		newSTTSink = sttFactory.NewSink
	}

	bridge := audiopipeline.NewBridge(newRecordSink, newSTTSink)
	pa.OnAudioPacket = bridge.HandlePacket
	pa.OnTrackEnded = bridge.HandleTrackEnded

	var player *ttsplayer.Player
	if publishTrack != nil && s.orch != nil {
		p, err := ttsplayer.New(publishTrack, ttsclient.New(s.cfg.TTSService.BaseURL), s.cfg.TTSService.SampleRate,
			s.recClient, s.roomID, s.cfg.AgentUserID, s.cfg.TTSService.MaxQueueSize, s.orch)
		if err != nil {
			// Best-effort per audio-ingestion/PLAN.md D5 -- an encoder
			// init failure (see internal/opusenc) must not stop the agent
			// from joining and record/transcribing normally, just means it
			// can't speak this session.
			logging.L.Warn("ttsplayer: unavailable this session", logging.ErrAttrs(err)...)
		} else {
			player = p
		}
	}

	s.peerAgent = pa
	s.player = player
	s.refs.set(bridge, player)
}

func (s *session) onOffer(sdp string) (string, error) {
	if s.peerAgent == nil {
		return "", errNoPeerAgent
	}
	return s.peerAgent.HandleOffer(sdp)
}

func (s *session) onRoomSnapshot(selfPeerID uint64, participantCount int, members []signaling.Member) {
	logging.L.Info("signaling: room_snapshot", "self_peer_id", selfPeerID, "participant_count", participantCount)
	if s.peerAgent == nil {
		return
	}
	for _, m := range members {
		s.peerAgent.UpsertRoster(m)
	}
}

func (s *session) onPeerJoined(participantCount int, peer signaling.Member) {
	logging.L.Info("signaling: peer_joined", "user_id", peer.UserID, "role", peer.Role, "participant_count", participantCount)
	if s.peerAgent != nil {
		s.peerAgent.UpsertRoster(peer)
	}
}

func (s *session) onPeerLeft(ev signaling.PeerLeftEvent) {
	logging.L.Info("signaling: peer_left", "user_id", ev.UserID, "participant_count", ev.ParticipantCount)
	if s.peerAgent != nil {
		s.peerAgent.RemovePeer(ev.UserID)
	}
}

func (s *session) onPeerUpdated(peer signaling.Member) {
	logging.L.Info("signaling: peer_updated", "user_id", peer.UserID, "role", peer.Role, "is_mute", peer.IsMute)
	if s.peerAgent != nil {
		s.peerAgent.UpsertRoster(peer)
	}
}

// close releases this session's resources, clears refs so the long-lived
// orchestrator SSE handlers stop seeing it as the active session, and
// unregisters the room from orchestrator (see RegisterRoom's doc for what
// that triggers -- room finalization/summary generation). Safe to call even
// if onJoined never fired (peerAgent/player stay nil) or registration never
// succeeded (roomID falls back to roomName -- unregistering that is a
// harmless no-op orchestrator-side, matching the old Python agent, which
// always called unregister_room in its cleanup path regardless).
func (s *session) close() {
	s.refs.set(nil, nil)
	if s.peerAgent != nil {
		_ = s.peerAgent.Close()
	}
	if s.player != nil {
		s.player.Close()
	}
	if s.orch != nil {
		// context.Background(), not runSession's ctx: this typically runs
		// during shutdown (SIGTERM), by which point that ctx is already
		// cancelled -- same pattern as transcriptForwarder.push.
		uctx, cancel := context.WithTimeout(context.Background(), orchestratorCallTimeout)
		defer cancel()
		if err := s.orch.UnregisterRoom(uctx, s.roomName, s.roomID); err != nil {
			logging.L.Warn("orchestratorclient: unregister_room failed", append(logging.ErrAttrs(err), "room_name", s.roomName)...)
		}
	}
}

var errNoPeerAgent = errors.New("received offer before joined/peer-connection setup completed")
