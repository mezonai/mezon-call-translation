// Command agent is the per-room mezon-sfu WebRTC client. It is spawned as a
// subprocess by the orchestrator's worker manager (mezon-sfu-migration-plan.md
// section 1) and does not know or care what triggered the spawn -- it only
// reads room_id/role/jwt_secret/user_id from the environment (internal/config),
// joins the room, and keeps the session alive until the process is killed or
// the WS session dies.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

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
	"github.com/mezonai/mezon-call-translation/agents/internal/sttclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsplayer"
)

func main() {
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
// cancelled. Every call builds brand new session state (rtcagent.PeerAgent,
// audiopipeline.Bridge, ttsplayer.Player if speaking) -- mezon-sfu ties the
// media session to the WS connection 1:1, so there is nothing to resume
// from a previous attempt, and mid numbering restarts from scratch too.
func runSession(ctx context.Context, cfg config.Config, recClient *recordclient.Client, orch *orchestratorclient.Client, refs *sessionRefs) error {
	token, err := sfuauth.SignJoinToken(cfg.JWTSecret, cfg.AgentUserID, cfg.RoomID, cfg.TokenTTL)
	if err != nil {
		return err
	}

	// peerAgent/player are set by OnJoined once mezon-sfu returns
	// iceServers, and read by OnOffer/roster callbacks. Safe without
	// locking: all signaling.Callbacks run sequentially on
	// signaling.Client's single read-loop goroutine, and OnJoined always
	// fires before offer per the join handshake order
	// (mezon-sfu-migration-checklist.md C.2).
	var peerAgent *rtcagent.PeerAgent
	var player *ttsplayer.Player

	cb := signaling.Callbacks{
		OnJoined: func(room uint64, iceServers []signaling.ICEServer) {
			logging.L.Info("signaling: joined", "room", room, "ice_servers", len(iceServers))

			var publishTrack *webrtc.TrackLocalStaticSample
			if cfg.Role == config.RoleSpeaker {
				track, err := webrtc.NewTrackLocalStaticSample(
					webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 1},
					"tts-audio", "agent-tts",
				)
				if err != nil {
					logging.L.Error("ttsplayer: failed to create publish track, joining as audience instead", logging.ErrAttrs(err)...)
				} else {
					publishTrack = track
				}
			}

			pa, err := rtcagent.New(iceServers, publishTrack)
			if err != nil {
				logging.L.Error("rtcagent: failed to create peer connection", logging.ErrAttrs(err)...)
				return
			}

			bridge := audiopipeline.NewBridge(newRecordSinkFactory(recClient, cfg), newSTTSinkFactory(cfg, orch))
			pa.OnAudioPacket = bridge.HandlePacket
			pa.OnTrackEnded = bridge.HandleTrackEnded

			if publishTrack != nil && orch != nil {
				p, err := ttsplayer.New(publishTrack, ttsclient.New(cfg.TTSService.BaseURL), cfg.TTSService.SampleRate,
					recClient, cfg.RoomID, cfg.AgentUserID, cfg.TTSService.MaxQueueSize)
				if err != nil {
					// Best-effort: no working Opus encoder yet (see
					// internal/opusenc) -- still join and record/transcribe
					// normally, just can't speak this session.
					logging.L.Warn("ttsplayer: unavailable this session", logging.ErrAttrs(err)...)
				} else {
					player = p
				}
			}

			peerAgent = pa
			refs.set(bridge, player)
		},
		OnOffer: func(sdp string) (string, error) {
			if peerAgent == nil {
				return "", errNoPeerAgent
			}
			return peerAgent.HandleOffer(sdp)
		},
		OnRoomSnapshot: func(selfPeerID uint64, participantCount int, members []signaling.Member) {
			logging.L.Info("signaling: room_snapshot", "self_peer_id", selfPeerID, "participant_count", participantCount)
			if peerAgent == nil {
				return
			}
			for _, m := range members {
				peerAgent.UpsertRoster(m)
			}
		},
		OnPeerJoined: func(participantCount int, peer signaling.Member) {
			logging.L.Info("signaling: peer_joined", "user_id", peer.UserID, "role", peer.Role, "participant_count", participantCount)
			if peerAgent != nil {
				peerAgent.UpsertRoster(peer)
			}
		},
		OnPeerLeft: func(ev signaling.PeerLeftEvent) {
			logging.L.Info("signaling: peer_left", "user_id", ev.UserID, "participant_count", ev.ParticipantCount)
			if peerAgent != nil {
				peerAgent.RemovePeer(ev.UserID)
			}
		},
		OnPeerUpdated: func(peer signaling.Member) {
			logging.L.Info("signaling: peer_updated", "user_id", peer.UserID, "role", peer.Role, "is_mute", peer.IsMute)
			if peerAgent != nil {
				peerAgent.UpsertRoster(peer)
			}
		},
	}

	client, err := signaling.Dial(ctx, cfg.SFUWebSocketURL, token, string(cfg.Role), cb)
	if err != nil {
		return err
	}
	defer func() {
		refs.set(nil, nil)
		_ = client.Close()
		if peerAgent != nil {
			_ = peerAgent.Close()
		}
		if player != nil {
			player.Close()
		}
	}()

	logging.L.Info("agent: dialed mezon-sfu, awaiting handshake",
		"ws_url", cfg.SFUWebSocketURL, "room_id", cfg.RoomID, "role", cfg.Role, "agent_user_id", cfg.AgentUserID)

	return client.Run(ctx)
}

// newRecordSinkFactory returns nil if recording is disabled for this run
// (recClient nil), so audiopipeline.NewBridge treats it as "no record sink
// for any track" without a nil-check at every call site.
func newRecordSinkFactory(recClient *recordclient.Client, cfg config.Config) func(info rtcagent.TrackInfo) audiopipeline.Sink {
	if recClient == nil {
		return nil
	}
	return func(info rtcagent.TrackInfo) audiopipeline.Sink {
		fwd, err := recordclient.NewForwarder(recClient, recordclient.SessionMeta{
			RoomID: strconv.FormatUint(cfg.RoomID, 10),
			// mezon-sfu has no persistent track SID like LiveKit's
			// publication SID (mezon-sfu-migration-checklist.md A3.6) --
			// peer_id+kind is unique for the life of that remote peer's WS
			// session, as stable an identifier as the protocol offers.
			TrackID:             fmt.Sprintf("peer%d-%s", info.PeerID, info.Kind),
			ParticipantIdentity: strconv.FormatInt(info.UserID, 10),
			Source:              string(info.Kind), // always "mic": OnAudioPacket only fires for KindAudio, see rtcagent
			SampleRate:          audiopipeline.PCMSampleRate,
			Channels:            audiopipeline.PCMChannels,
		}, cfg.RecordService.MaxQueueSize)
		if err != nil {
			// Best-effort per audio-ingestion/PLAN.md D5 -- no recording
			// for this track must never be fatal to the call/STT.
			logging.L.Error("recordclient: failed to start forwarder", append(logging.ErrAttrs(err), "mid", info.Mid)...)
			return nil
		}
		return fwd
	}
}

// newSTTSinkFactory returns nil if STT can't do anything useful this run
// (no STT host configured, or no orchestrator to push transcripts to and
// no way it could ever be enabled -- see registerRequestHandlers, which
// only registers transcript_control when orch is non-nil).
func newSTTSinkFactory(cfg config.Config, orch *orchestratorclient.Client) func(info rtcagent.TrackInfo) audiopipeline.Sink {
	if cfg.STT.Host == "" || orch == nil {
		return nil
	}
	roomName := strconv.FormatUint(cfg.RoomID, 10) // see orchestratorclient's package doc re: room_name vs room_id

	return func(info rtcagent.TrackInfo) audiopipeline.Sink {
		clientID := fmt.Sprintf("peer%d-%s", info.PeerID, info.Kind)
		participantIdentity := strconv.FormatInt(info.UserID, 10)
		wsURL := fmt.Sprintf("ws://%s:%d/ws/vosk/?client_id=%s&session_id=%s",
			cfg.STT.Host, cfg.STT.Port, url.QueryEscape(clientID), url.QueryEscape(roomName))

		onMessage := func(raw []byte) {
			text, isFinal, ok := parseTranscriptMessage(raw)
			if !ok || text == "" {
				return
			}
			messageType := "PARTIAL"
			if isFinal {
				messageType = "FINAL"
			}
			// Fire-and-forget, off the STT client's own read-loop goroutine
			// -- pushing to orchestrator must never delay reading the next
			// transcription result.
			go func() {
				pctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := orch.PushTranscript(pctx, roomName, text, participantIdentity, messageType); err != nil {
					logging.L.Warn("orchestratorclient: push_transcript failed", logging.ErrAttrs(err)...)
				}
			}()
		}

		c, err := sttclient.Dial(context.Background(), wsURL, cfg.STT.MaxQueueSize, onMessage)
		if err != nil {
			logging.L.Error("sttclient: failed to dial", append(logging.ErrAttrs(err), "mid", info.Mid)...)
			return nil
		}
		return c
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

var errNoPeerAgent = errors.New("received offer before joined/peer-connection setup completed")
