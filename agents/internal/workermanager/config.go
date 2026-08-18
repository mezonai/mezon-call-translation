// Package workermanager implements the "worker manager" from
// mezon-sfu-migration-plan.md section 1: it subscribes to the NATS
// start/stop events BE mezon publishes and spawns/kills one `agent`
// subprocess per room_id.
//
// It deliberately lives in this same module/repo as the `agent` binary it
// spawns (cmd/agent) rather than inside orchestrator_service, mirroring how
// LiveKit's own `agents` framework keeps the worker (dispatch/process
// management) and the job/agent behavior in one codebase -- LiveKit just has
// an SDK that makes that pairing more ergonomic. Two concrete reasons this
// isn't part of orchestrator_service:
//   - orchestrator_service's room registry is Redis-backed, i.e. it's built
//     to run as multiple replicas. A NATS start/stop event would then need a
//     queue group PLUS extra coordination to route "stop" back to whichever
//     replica holds the subprocess PID -- complexity this package avoids by
//     being its own single-purpose process.
//   - orchestrator_service (API/webhook tier) redeploys more often than an
//     agent's lifetime; coupling agent subprocess lifetime to that deploy
//     cadence risks killing active call recordings on every API deploy.
package workermanager

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type Config struct {
	NATSURL string
	// StartSubject/StopSubject/QueueGroup: subject names are NOT finalized
	// with BE mezon yet (mezon-sfu-migration-plan.md section 1) -- these are
	// placeholders to unblock coding, expect to change.
	StartSubject string
	StopSubject  string
	QueueGroup   string
	// AgentBinPath is the path to the built `agent` binary (cmd/agent) this
	// manager spawns per room.
	AgentBinPath string
	// StopTimeout: how long to wait after SIGTERM before escalating to
	// SIGKILL.
	StopTimeout time.Duration
	// BaseAgentEnv is applied to every spawned agent (mezon-sfu connection
	// details, reconnect tuning...) -- same for every room in a given
	// deployment. Per-room fields (ROOM_ID/AGENT_USER_ID/AGENT_ROLE) come
	// from the NATS event and are added on top at spawn time.
	BaseAgentEnv []string
}

// agentPassthroughEnvKeys are copied from this process's environment into
// every spawned agent's environment, unchanged. Keep in sync with
// internal/config.Config's env vars.
var agentPassthroughEnvKeys = []string{
	"SFU_WS_URL",
	"SFU_JWT_SECRET",
	"AGENT_TOKEN_TTL_SECONDS",
	"AGENT_RECONNECT_MAX_ATTEMPTS",
	"AGENT_RECONNECT_BASE_DELAY_MS",
	"AGENT_RECONNECT_MAX_DELAY_MS",
	"AGENT_RECONNECT_STABLE_AFTER_SECONDS",
	"RECORD_SERVICE_GRPC_ADDR",
	"RECORD_SERVICE_MAX_QUEUE_SIZE",
	"WS_HOST",
	"WS_PORT",
	"STT_MAX_QUEUE_SIZE",
	"ORCHESTRATOR_BASE_URL",
	"INTERNAL_API_SECRET",
	"TTS_SERVICE_BASE_URL",
	"TTS_SAMPLE_RATE",
	"TTS_RECORD_MAX_QUEUE_SIZE",
	"LOG_LEVEL",
}

func FromEnv() (Config, error) {
	cfg := Config{
		NATSURL:      getEnv("NATS_URL", "nats://127.0.0.1:4222"),
		StartSubject: getEnv("AGENT_START_SUBJECT", "mezon.agent.start"),
		StopSubject:  getEnv("AGENT_STOP_SUBJECT", "mezon.agent.stop"),
		QueueGroup:   getEnv("AGENT_WORKER_QUEUE_GROUP", "agent-worker-manager"),
		AgentBinPath: getEnv("AGENT_BIN_PATH", defaultAgentBinPath()),
	}

	stopTimeoutSec, err := strconv.Atoi(getEnv("AGENT_STOP_TIMEOUT_SECONDS", "10"))
	if err != nil {
		return Config{}, fmt.Errorf("workermanager: invalid AGENT_STOP_TIMEOUT_SECONDS: %w", err)
	}
	cfg.StopTimeout = time.Duration(stopTimeoutSec) * time.Second
	cfg.BaseAgentEnv = passthroughEnv(agentPassthroughEnvKeys)

	return cfg, nil
}

// defaultAgentBinPath assumes the `agent` binary is built alongside this
// one (e.g. both dropped in the same bin/ directory by the build). Override
// with AGENT_BIN_PATH if that's not how it's deployed.
func defaultAgentBinPath() string {
	if exe, err := os.Executable(); err == nil {
		return filepath.Join(filepath.Dir(exe), "agent")
	}
	return "./agent"
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func passthroughEnv(keys []string) []string {
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		if v, ok := os.LookupEnv(k); ok {
			out = append(out, k+"="+v)
		}
	}
	return out
}
