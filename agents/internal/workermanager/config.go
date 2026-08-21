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
	// Subject: BOTH add and delete are published on this single subject,
	// distinguished by the "action" field, not by subject.
	//
	// [2026-08-20, corrected] The actual wire subject is the NATS *string*
	// "mezon_sfu_hook_event", NOT the literal text "SFU_HOOK_EVENT". BE
	// mezon's Go code names its constant `SFU_HOOK_EVENT`, but that
	// constant's *value* is `"mezon_sfu_hook_event"`:
	//
	//   const SFU_HOOK_EVENT = "mezon_sfu_hook_event"
	//   func dispatchSfuAgentMessage(nc *nats.Conn, buf []byte) error {
	//       return nc.Publish(SFU_HOOK_EVENT, buf)
	//   }
	//
	// The 2026-08-19 confirmation mistook the Go identifier name for the
	// subject string and set the default below to the literal
	// "SFU_HOOK_EVENT" -- which meant this subscription would never have
	// received a real BE mezon dispatch event at all. Conveniently, this
	// now also matches mezon-sfu's own hook-event default as of its commit
	// 88984d6 (2026-08-20, `nats_hook_topic` default -> also
	// "mezon_sfu_hook_event") -- so both BE mezon's dispatch and
	// mezon-sfu's own participant hook events land on the same real
	// subject again, same as originally assumed, just under the right
	// string. See dispatchEvent's doc in events.go and
	// mezon-sfu-migration-checklist.md D4.
	Subject    string
	QueueGroup string
	// AgentBinPath is the path to the built `agent` binary (cmd/agent) this
	// manager spawns per room.
	AgentBinPath string
	// StopTimeout: how long to wait after SIGTERM before escalating to
	// SIGKILL. Runs in the background (manager.go's killAfterTimeout), not
	// gating anything else -- see Stop's [2026-08-21] doc for why it's safe
	// for this to be generous. Should stay comfortably above the agent's own
	// graceful-shutdown worst case (~16s as of the trackWG/ttsplayer close
	// grace added 2026-08-20/21 -- see agents/README.md's shutdown timeout
	// bullet) so SIGKILL doesn't cut off recording/orchestrator cleanup that
	// was already running.
	StopTimeout time.Duration
	// BaseAgentEnv is applied to every spawned agent (mezon-sfu connection
	// details, reconnect tuning...) -- same for every room in a given
	// deployment. Per-room fields (ROOM_ID/AGENT_USER_ID/AGENT_ROLE) are
	// added on top at spawn time -- ROOM_ID and (optionally) AGENT_ROLE
	// from the dispatch event, AGENT_USER_ID always derived from
	// AgentUserIDBase below, never from the event.
	BaseAgentEnv []string
	// AgentUserIDBase: interim, explicitly configured workaround for the
	// still-unresolved agent_user_id gap (mezon-sfu-migration-checklist.md
	// D4/B1) -- the real dispatch event never carries agent_user_id, so
	// every spawned agent uses this one fixed id instead (checked: nothing
	// in mezon-sfu enforces user_id uniqueness across rooms -- it's scoped
	// per WS connection/room, not global -- so one shared id for every
	// concurrently running agent is fine, same as a normal bot account
	// having one identity across every channel it's in). Leave at 0
	// (default) to keep the old hard-fail behavior. Must be picked to not
	// collide with real Mezon user ids -- coordinate the actual value with
	// BE mezon, this is not a final decision, just enough to unblock local
	// testing.
	AgentUserIDBase int64
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
		Subject:      getEnv("AGENT_DISPATCH_SUBJECT", "mezon_sfu_hook_event"),
		QueueGroup:   getEnv("AGENT_WORKER_QUEUE_GROUP", "agent-worker-manager"),
		AgentBinPath: getEnv("AGENT_BIN_PATH", defaultAgentBinPath()),
	}

	stopTimeoutSec, err := strconv.Atoi(getEnv("AGENT_STOP_TIMEOUT_SECONDS", "20"))
	if err != nil {
		return Config{}, fmt.Errorf("workermanager: invalid AGENT_STOP_TIMEOUT_SECONDS: %w", err)
	}
	cfg.StopTimeout = time.Duration(stopTimeoutSec) * time.Second

	agentUserIDBase, err := strconv.ParseInt(getEnv("AGENT_USER_ID_BASE", "0"), 10, 64)
	if err != nil {
		return Config{}, fmt.Errorf("workermanager: invalid AGENT_USER_ID_BASE: %w", err)
	}
	cfg.AgentUserIDBase = agentUserIDBase

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
