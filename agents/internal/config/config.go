// Package config loads the agent's runtime parameters. The agent is spawned
// as a subprocess by the orchestrator's worker manager (one subprocess per
// room); it does not know or care what triggered the spawn (NATS event vs.
// anything else) -- it only needs these values, passed via environment.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Role mirrors the `role` field of the mezon-sfu `join` message.
type Role string

const (
	// RoleAudience: subscribe-only, used for the record-forwarding path.
	RoleAudience Role = "audience"
	// RoleSpeaker: can also publish (TTS talk-back).
	RoleSpeaker Role = "speaker"
)

type Config struct {
	// SFUWebSocketURL is the mezon-sfu signaling endpoint, e.g. ws://127.0.0.1:8000/ws.
	SFUWebSocketURL string
	// JWTSecret signs the join token (HS256). mezon-sfu isn't validating the
	// signature yet as of 2026-08-17 (secret to be shared later) -- see
	// mezon-sfu-migration-plan.md section 0. Keep this swappable.
	JWTSecret string
	// RoomID is the only source of truth for room membership per the
	// 2026-08-16 mezon-sfu protocol update -- it travels in the JWT `room`
	// claim, not in the `join` message body.
	RoomID uint64
	// AgentUserID is the identity the agent presents as (JWT `identity` claim).
	AgentUserID int64
	// Role: "audience" (record-only, default) or "speaker" (also publishes TTS).
	Role Role
	// TokenTTL controls the JWT `exp` claim.
	TokenTTL time.Duration
	// Reconnect controls in-process retry after the WS session dies (e.g. a
	// brief network glitch). This is always a full rejoin (new JWT, new SDP/
	// ICE/DTLS) -- mezon-sfu ties the media session to the WS connection, so
	// there's no "resume". It is not a substitute for process-level
	// supervision: once MaxAttempts is exhausted the process exits non-zero
	// so whatever manages the subprocess (worker manager) can take over.
	Reconnect ReconnectConfig
	// RecordService: gRPC forwarding target. Recording is best-effort --
	// if unset/unreachable, the agent logs and keeps running without it
	// rather than failing to join the room (audio-ingestion/PLAN.md D5).
	RecordService RecordServiceConfig
	// STT: Vosk transcription service this agent's audiopipeline forwards
	// decoded PCM to, gated by AgentControlState (default disabled --
	// enabled via the `transcript_control` request over the orchestrator
	// SSE connection, see internal/orchestratorclient).
	STT STTConfig
	// Orchestrator: base URL/API key for push_transcript and the SSE
	// agent-request listener (tts_play, transcript_control). Optional --
	// if BaseURL is empty, the agent runs without it (no transcript push,
	// no TTS trigger, no transcript_control toggle -- effectively record-only).
	Orchestrator OrchestratorConfig
	// TTSService: HTTP endpoint synthesizing text to PCM. Only relevant
	// when Role is "speaker" -- see internal/ttsplayer.
	TTSService TTSServiceConfig
	// ControlSocketDir is where this agent listens on a per-room Unix
	// domain socket (internal/agentcontrol.Listen), so a worker-manager
	// that restarts later can reconnect to it (mezon-sfu-migration-plan.md
	// gap "Chưa làm, biết trước là gap", Phase 1 -- worker-manager's own
	// dial-back-in side is Phase 3, not implemented yet). Must match
	// worker-manager's AGENT_SOCKET_DIR -- passed through via
	// internal/workermanager/config.go's agentPassthroughEnvKeys, not
	// something to set independently per room.
	ControlSocketDir string
	// MaxLifetime bounds how long this agent runs before shutting itself
	// down, win or lose -- a self-imposed upper bound on how long a call can
	// be translated for, tied to pricing plans (mezon-sfu-migration-plan.md).
	// 0 disables it (runs indefinitely, e.g. local/manual testing).
	//
	// This is deliberately enforced *inside* the agent, not by worker-manager
	// watching a clock and sending SIGTERM at the right time: worker-manager
	// can itself be down when the deadline is reached (crash, redeploy,
	// exactly the restart-recovery gap this whole plan section exists for),
	// and an adopted agent (Phase 3, reconcile.go) is reparented away from
	// the instance that spawned it anyway. A timer inside the agent's own
	// process keeps running regardless of whether *anything* worker-manager
	// side is alive to enforce it.
	//
	// Firing this just cancels the same context SIGINT/SIGTERM cancel
	// (cmd/agent/main.go) -- the exact same graceful-shutdown path, not a
	// separate one. No additional "did it actually stop in time" backstop
	// timer needed here: every step of that path (rtcagent.PeerAgent.Close,
	// ttsplayer.Player.Close, recordclient.Forwarder.Close,
	// orchestratorclient's HTTP calls) is already individually bounded by its
	// own timeout, so the existing chain's documented worst case (~16-20s,
	// see workermanager.Config.StopTimeout's doc) already holds here the same
	// as it does for a worker-manager-initiated SIGTERM -- discussed and
	// confirmed in mezon-sfu-migration-plan.md before deciding against a
	// second internal force-exit timer.
	//
	// Worker-manager sets this explicitly per spawn (not a passthrough env,
	// see agentPassthroughEnvKeys) so it can later be overridden per-event
	// once BE mezon starts sending a plan-derived value over NATS; until
	// then every agent gets the same configured default. Read here too (not
	// only worker-manager-side) so a standalone/local run still gets a
	// sane bound instead of silently running forever.
	MaxLifetime time.Duration
	// EmptyRoomGrace: how long to keep running after this agent becomes the
	// only member left in the room (participant_count == 1, mezon-sfu counts
	// the agent itself same as any real user -- mezon-sfu/CLAUDE.md section
	// 9) before shutting itself down gracefully -- the session/interview is
	// over once everyone real has left. 0 disables it (never self-exits on
	// an empty room).
	//
	// Deliberately a grace period, not immediate: participant_count also
	// drops on a transient network blip (the last human's own client
	// reconnecting), not just a deliberate leave -- see cmd/agent/main.go's
	// session.checkEmptyRoom doc for the full race analysis (in particular
	// why this is independent of, and much shorter than, this agent's own
	// Reconnect budget).
	EmptyRoomGrace time.Duration
}

type ReconnectConfig struct {
	// MaxAttempts is how many consecutive failed sessions to retry before
	// giving up and exiting. 0 disables reconnect entirely (first failure exits).
	MaxAttempts int
	// BaseDelay/MaxDelay bound the exponential backoff between attempts.
	BaseDelay time.Duration
	MaxDelay  time.Duration
	// StableAfter: a session that stayed up at least this long before dying
	// resets the attempt counter -- so a connection that's fine 99% of the
	// time doesn't slowly burn through the whole retry budget one glitch at
	// a time and then refuse to recover from a later, unrelated glitch.
	StableAfter time.Duration
}

// RecordServiceConfig mirrors the old Python agent's RecordServiceConfig
// (Architect_MultiClient_Server/agents/src/config/application_config.py) --
// same env var names, same defaults, so ops tooling/dashboards that already
// know these names keep working.
type RecordServiceConfig struct {
	GRPCAddr     string
	MaxQueueSize int
}

// STTConfig mirrors the (actually-used subset of the) old Python agent's
// STTServiceConfig -- Host/Port only. The old config also carried
// auth_token/api_key/auth_header, but grepping the old WS base client
// confirmed they were never attached to the connection, so this port
// doesn't carry over dead config surface.
type STTConfig struct {
	Host         string
	Port         int
	MaxQueueSize int
}

// OrchestratorConfig mirrors the old Python agent's OrchestratorConfig.
type OrchestratorConfig struct {
	BaseURL string
	APIKey  string
}

// TTSServiceConfig mirrors the old Python agent's TTSConfig.
type TTSServiceConfig struct {
	BaseURL      string
	SampleRate   int
	MaxQueueSize int
}

func FromEnv() (Config, error) {
	cfg := Config{
		SFUWebSocketURL:  getEnv("SFU_WS_URL", "ws://127.0.0.1:8000/ws"),
		JWTSecret:        getEnv("SFU_JWT_SECRET", "default"),
		Role:             Role(getEnv("AGENT_ROLE", string(RoleAudience))),
		ControlSocketDir: getEnv("AGENT_SOCKET_DIR", "/tmp/mezon-agents"),
	}

	roomID, err := requireUint64Env("ROOM_ID")
	if err != nil {
		return Config{}, err
	}
	cfg.RoomID = roomID

	userID, err := requireInt64Env("AGENT_USER_ID")
	if err != nil {
		return Config{}, err
	}
	cfg.AgentUserID = userID

	ttlSeconds, err := strconv.Atoi(getEnv("AGENT_TOKEN_TTL_SECONDS", "21600")) // 6h
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_TOKEN_TTL_SECONDS: %w", err)
	}
	cfg.TokenTTL = time.Duration(ttlSeconds) * time.Second

	maxLifetimeSeconds, err := strconv.Atoi(getEnv("AGENT_MAX_LIFETIME_SECONDS", "10800")) // 3h
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_MAX_LIFETIME_SECONDS: %w", err)
	}
	if maxLifetimeSeconds < 0 {
		return Config{}, fmt.Errorf("config: AGENT_MAX_LIFETIME_SECONDS must be >= 0, got %d", maxLifetimeSeconds)
	}
	cfg.MaxLifetime = time.Duration(maxLifetimeSeconds) * time.Second

	emptyRoomGraceSeconds, err := strconv.Atoi(getEnv("AGENT_EMPTY_ROOM_GRACE_SECONDS", "15"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_EMPTY_ROOM_GRACE_SECONDS: %w", err)
	}
	if emptyRoomGraceSeconds < 0 {
		return Config{}, fmt.Errorf("config: AGENT_EMPTY_ROOM_GRACE_SECONDS must be >= 0, got %d", emptyRoomGraceSeconds)
	}
	cfg.EmptyRoomGrace = time.Duration(emptyRoomGraceSeconds) * time.Second

	if cfg.Role != RoleAudience && cfg.Role != RoleSpeaker {
		return Config{}, fmt.Errorf("config: invalid AGENT_ROLE %q (want %q or %q)", cfg.Role, RoleAudience, RoleSpeaker)
	}

	maxAttempts, err := strconv.Atoi(getEnv("AGENT_RECONNECT_MAX_ATTEMPTS", "8"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_RECONNECT_MAX_ATTEMPTS: %w", err)
	}
	baseDelayMs, err := strconv.Atoi(getEnv("AGENT_RECONNECT_BASE_DELAY_MS", "1000"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_RECONNECT_BASE_DELAY_MS: %w", err)
	}
	maxDelayMs, err := strconv.Atoi(getEnv("AGENT_RECONNECT_MAX_DELAY_MS", "30000"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_RECONNECT_MAX_DELAY_MS: %w", err)
	}
	stableAfterSeconds, err := strconv.Atoi(getEnv("AGENT_RECONNECT_STABLE_AFTER_SECONDS", "30"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid AGENT_RECONNECT_STABLE_AFTER_SECONDS: %w", err)
	}
	if maxAttempts < 0 {
		return Config{}, fmt.Errorf("config: AGENT_RECONNECT_MAX_ATTEMPTS must be >= 0, got %d", maxAttempts)
	}
	cfg.Reconnect = ReconnectConfig{
		MaxAttempts: maxAttempts,
		BaseDelay:   time.Duration(baseDelayMs) * time.Millisecond,
		MaxDelay:    time.Duration(maxDelayMs) * time.Millisecond,
		StableAfter: time.Duration(stableAfterSeconds) * time.Second,
	}

	recordMaxQueueSize, err := strconv.Atoi(getEnv("RECORD_SERVICE_MAX_QUEUE_SIZE", "200"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid RECORD_SERVICE_MAX_QUEUE_SIZE: %w", err)
	}
	if recordMaxQueueSize <= 0 {
		return Config{}, fmt.Errorf("config: RECORD_SERVICE_MAX_QUEUE_SIZE must be > 0, got %d", recordMaxQueueSize)
	}
	cfg.RecordService = RecordServiceConfig{
		GRPCAddr:     getEnv("RECORD_SERVICE_GRPC_ADDR", "record-service:50051"),
		MaxQueueSize: recordMaxQueueSize,
	}

	sttPort, err := strconv.Atoi(getEnv("WS_PORT", "8000"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid WS_PORT: %w", err)
	}
	sttMaxQueueSize, err := strconv.Atoi(getEnv("STT_MAX_QUEUE_SIZE", "32"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid STT_MAX_QUEUE_SIZE: %w", err)
	}
	if sttMaxQueueSize <= 0 {
		return Config{}, fmt.Errorf("config: STT_MAX_QUEUE_SIZE must be > 0, got %d", sttMaxQueueSize)
	}
	cfg.STT = STTConfig{
		Host:         getEnv("WS_HOST", "localhost"),
		Port:         sttPort,
		MaxQueueSize: sttMaxQueueSize,
	}

	cfg.Orchestrator = OrchestratorConfig{
		BaseURL: getEnv("ORCHESTRATOR_BASE_URL", "http://localhost:8002"),
		APIKey:  getEnv("INTERNAL_API_SECRET", ""),
	}

	ttsSampleRate, err := strconv.Atoi(getEnv("TTS_SAMPLE_RATE", "24000"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid TTS_SAMPLE_RATE: %w", err)
	}
	ttsMaxQueueSize, err := strconv.Atoi(getEnv("TTS_RECORD_MAX_QUEUE_SIZE", "200"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid TTS_RECORD_MAX_QUEUE_SIZE: %w", err)
	}
	cfg.TTSService = TTSServiceConfig{
		BaseURL:      getEnv("TTS_SERVICE_BASE_URL", "http://localhost:8008"),
		SampleRate:   ttsSampleRate,
		MaxQueueSize: ttsMaxQueueSize,
	}

	return cfg, nil
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func requireUint64Env(key string) (uint64, error) {
	v := os.Getenv(key)
	if v == "" {
		return 0, fmt.Errorf("config: missing required env %s", key)
	}
	n, err := strconv.ParseUint(v, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("config: invalid %s=%q: %w", key, v, err)
	}
	return n, nil
}

func requireInt64Env(key string) (int64, error) {
	v := os.Getenv(key)
	if v == "" {
		return 0, fmt.Errorf("config: missing required env %s", key)
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("config: invalid %s=%q: %w", key, v, err)
	}
	return n, nil
}
