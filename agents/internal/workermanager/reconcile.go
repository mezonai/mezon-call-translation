package workermanager

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/agentcontrol"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// dialTimeout bounds how long Reconcile waits for a candidate agent's
// control socket to accept a connection. Short on purpose: a genuinely
// live agent's listener (internal/agentcontrol.Listen, opened at the
// agent's own startup, not lazily) accepts near-instantly -- anything
// slower means something's already wrong, and waiting longer only delays
// worker-manager's own startup for no benefit.
const dialTimeout = 2 * time.Second

// Reconcile re-adopts agents that a previous instance of this
// worker-manager spawned and are still running (crash, redeploy, manual
// restart) -- Phase 3 of mezon-sfu-migration-plan.md's restart-recovery
// gap. Call once at startup, before subscribing to NATS
// (cmd/worker-manager/main.go), so no Start/Stop for a room this is about
// to adopt can race the reconciliation itself.
//
// Every entry is handled best-effort: a missing registry file (first run,
// or a clean shutdown that never wrote one -- Shutdown doesn't stop
// managed agents, see its doc, but also doesn't clear the file) is not an
// error, and a stale/dead/mismatched individual entry is dropped silently
// (logged at Info/Warn, not fatal) -- these are all expected outcomes of
// "the agent already exited on its own before this instance got a chance
// to look," not failure conditions worth stopping startup over.
func (m *Manager) Reconcile() {
	entries, err := m.readRegistryFile()
	if err != nil {
		if !os.IsNotExist(err) {
			logging.L.Error("workermanager: failed to read registry for reconciliation, starting with no adopted agents",
				logging.ErrAttrs(err)...)
		}
		return
	}

	adopted := 0
	for _, e := range entries {
		agent, ok := m.tryAdopt(e)
		if !ok {
			continue
		}
		m.mu.Lock()
		m.agents[e.RoomID] = agent
		m.mu.Unlock()
		go m.reap(agent)
		adopted++
	}

	logging.L.Info("workermanager: reconciliation complete", "entries_found", len(entries), "adopted", adopted)
	// Rewrite the file now so stale entries (pid dead, pid reused, socket
	// unreachable) don't linger until some unrelated room's Start/Stop
	// happens to overwrite them later.
	m.persistRegistry()
}

func (m *Manager) readRegistryFile() ([]persistedAgent, error) {
	data, err := os.ReadFile(m.registryPath())
	if err != nil {
		return nil, err
	}
	var reg persistedRegistry
	if err := json.Unmarshal(data, &reg); err != nil {
		return nil, fmt.Errorf("workermanager: parse registry %s: %w", m.registryPath(), err)
	}
	return reg.Agents, nil
}

// tryAdopt verifies one registry entry still refers to a live, genuine
// agent process and, if so, reconnects to its control socket. A false
// return means the entry should be treated as stale (reason already
// logged) -- never a reason to abort the rest of Reconcile.
func (m *Manager) tryAdopt(e persistedAgent) (*managedAgent, bool) {
	if !processAlive(e.PID) {
		logging.L.Info("workermanager: reconcile skip, pid no longer alive", "room_id", e.RoomID, "pid", e.PID)
		return nil, false
	}
	if !processIsAgentBinary(e.PID, m.cfg.AgentBinPath) {
		logging.L.Warn("workermanager: reconcile skip, pid was reused by a different process",
			"room_id", e.RoomID, "pid", e.PID)
		return nil, false
	}

	path := agentcontrol.SocketPath(m.cfg.SocketDir, e.RoomID)
	conn, err := net.DialTimeout("unix", path, dialTimeout)
	if err != nil {
		logging.L.Warn("workermanager: reconcile skip, control socket unreachable",
			append(logging.ErrAttrs(err), "room_id", e.RoomID, "pid", e.PID, "socket_path", path)...)
		return nil, false
	}

	// os.FindProcess never actually fails on Unix (it just wraps the pid,
	// no lookup happens) -- handled anyway rather than assumed, in case
	// that ever changes or this runs somewhere it doesn't hold.
	process, err := os.FindProcess(e.PID)
	if err != nil {
		_ = conn.Close()
		logging.L.Error("workermanager: reconcile skip, FindProcess failed",
			append(logging.ErrAttrs(err), "room_id", e.RoomID, "pid", e.PID)...)
		return nil, false
	}

	logging.L.Info("workermanager: reconcile adopted agent",
		"room_id", e.RoomID, "pid", e.PID, "agent_user_id", e.AgentUserID)
	return &managedAgent{
		process:     process,
		conn:        conn,
		roomID:      e.RoomID,
		agentUserID: e.AgentUserID,
		startedAt:   e.StartedAt,
		done:        make(chan struct{}),
	}, true
}

// processAlive reports whether pid refers to a live process, via the
// signal-0 idiom: sending signal 0 delivers nothing but still runs the
// kernel's permission/existence checks, failing with ESRCH if the pid
// doesn't exist. Doesn't distinguish "gone" from "exists but we lack
// permission to signal it" (EPERM) -- worker-manager and every agent it
// spawns run as the same user, so EPERM shouldn't happen here in practice;
// treating it the same as ESRCH (both "can't adopt") is the safe default
// either way.
func processAlive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

// processIsAgentBinary guards against the pid having been recycled by an
// unrelated process since the registry entry was written -- rare, but real
// on Linux, which reuses pids once a process exits. Compares
// /proc/<pid>/exe (the running binary's resolved path) against the
// configured agent binary path, both as absolute paths so a relative
// AGENT_BIN_PATH still compares correctly.
func processIsAgentBinary(pid int, agentBinPath string) bool {
	want, err := filepath.Abs(agentBinPath)
	if err != nil {
		return false
	}
	got, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
	if err != nil {
		// Process gone between the alive-check and here, or /proc
		// unavailable -- either way, "can't verify" is not "safe to
		// adopt".
		return false
	}
	return got == want
}
