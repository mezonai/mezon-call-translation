package workermanager

import (
	"fmt"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/config"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

type managedAgent struct {
	cmd         *exec.Cmd
	roomID      uint64
	agentUserID int64
	startedAt   time.Time
	done        chan struct{} // closed once cmd.Wait() returns
}

// Manager owns the in-memory room_id -> subprocess mapping for agents
// spawned by this process. It intentionally keeps no state on disk: if this
// process restarts, already-spawned agents keep running (own process group,
// see Start) but this new instance has no way to address them individually
// anymore (a later Stop for that room_id will just log "not found" and be a
// no-op). That's a known gap, not a bug -- see Shutdown's doc. Revisit with
// persistence (e.g. a local state file, PID liveness reconciliation on
// startup) if that turns out to matter in practice; not worth the added
// complexity before there's a real deployment to size it against.
type Manager struct {
	cfg Config

	mu     sync.Mutex
	agents map[uint64]*managedAgent
}

func New(cfg Config) *Manager {
	return &Manager{cfg: cfg, agents: make(map[uint64]*managedAgent)}
}

// Start spawns one `agent` subprocess for ev.RoomID. Idempotent: a start
// for a room that already has a running agent is logged and ignored rather
// than double-spawning (a duplicate/retried NATS delivery shouldn't produce
// two agents fighting over the same room).
func (m *Manager) Start(ev StartEvent) error {
	if ev.RoomID == 0 {
		return fmt.Errorf("workermanager: start event missing room_id")
	}
	if ev.AgentUserID == 0 {
		return fmt.Errorf("workermanager: start event for room_id=%d missing agent_user_id", ev.RoomID)
	}
	role := ev.Role
	if role == "" {
		role = string(config.RoleAudience)
	}

	m.mu.Lock()
	if existing, ok := m.agents[ev.RoomID]; ok {
		m.mu.Unlock()
		logging.L.Warn("workermanager: start ignored, agent already running for room",
			"room_id", ev.RoomID, "pid", existing.cmd.Process.Pid)
		return nil
	}
	m.mu.Unlock()

	env := make([]string, 0, len(m.cfg.BaseAgentEnv)+3)
	env = append(env, m.cfg.BaseAgentEnv...)
	env = append(env,
		fmt.Sprintf("ROOM_ID=%d", ev.RoomID),
		fmt.Sprintf("AGENT_USER_ID=%d", ev.AgentUserID),
		fmt.Sprintf("AGENT_ROLE=%s", role),
	)

	cmd := exec.Command(m.cfg.AgentBinPath)
	cmd.Env = env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	// Own process group: a signal delivered to this manager's process group
	// (e.g. Ctrl-C in a terminal running both in the foreground) must not
	// cascade to the child. Only an explicit Stop() call should ever
	// terminate an agent.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("workermanager: spawn agent for room_id=%d: %w", ev.RoomID, err)
	}

	agent := &managedAgent{
		cmd:         cmd,
		roomID:      ev.RoomID,
		agentUserID: ev.AgentUserID,
		startedAt:   time.Now(),
		done:        make(chan struct{}),
	}

	m.mu.Lock()
	m.agents[ev.RoomID] = agent
	m.mu.Unlock()

	logging.L.Info("workermanager: spawned agent",
		"room_id", ev.RoomID, "agent_user_id", ev.AgentUserID, "role", role, "pid", cmd.Process.Pid)

	go m.reap(agent)
	return nil
}

// reap waits for a spawned agent to exit and removes it from the registry.
// This is what makes an agent that exhausts its own reconnect budget (see
// internal/reconnect) or otherwise exits on its own clean itself out of
// this manager's bookkeeping, not just processes we explicitly Stop().
func (m *Manager) reap(a *managedAgent) {
	err := a.cmd.Wait()
	close(a.done)

	m.mu.Lock()
	if cur, ok := m.agents[a.roomID]; ok && cur == a {
		delete(m.agents, a.roomID)
	}
	m.mu.Unlock()

	logging.L.Info("workermanager: agent process exited",
		append(logging.ErrAttrs(err), "room_id", a.roomID, "pid", a.cmd.Process.Pid, "uptime", time.Since(a.startedAt))...)
}

// Stop signals the agent for ev.RoomID to shut down: SIGTERM first (the
// agent's own signal handler treats this as "leave the room", see
// cmd/agent/main.go), escalating to SIGKILL if it hasn't exited within
// cfg.StopTimeout. A room with no running agent is logged and treated as
// success (already in the desired end state).
func (m *Manager) Stop(ev StopEvent) error {
	if ev.RoomID == 0 {
		return fmt.Errorf("workermanager: stop event missing room_id")
	}

	m.mu.Lock()
	agent, ok := m.agents[ev.RoomID]
	m.mu.Unlock()
	if !ok {
		logging.L.Warn("workermanager: stop ignored, no agent running for room", "room_id", ev.RoomID)
		return nil
	}

	logging.L.Info("workermanager: stopping agent", "room_id", ev.RoomID, "pid", agent.cmd.Process.Pid)
	if err := agent.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		return fmt.Errorf("workermanager: sigterm room_id=%d pid=%d: %w", ev.RoomID, agent.cmd.Process.Pid, err)
	}

	select {
	case <-agent.done:
		return nil
	case <-time.After(m.cfg.StopTimeout):
		logging.L.Warn("workermanager: agent did not exit within stop timeout, sending SIGKILL",
			"room_id", ev.RoomID, "pid", agent.cmd.Process.Pid, "timeout", m.cfg.StopTimeout)
		if err := agent.cmd.Process.Kill(); err != nil {
			return fmt.Errorf("workermanager: sigkill room_id=%d pid=%d: %w", ev.RoomID, agent.cmd.Process.Pid, err)
		}
		// Block until reap() actually removes this room from the registry
		// (SIGKILL isn't interceptable, so cmd.Wait() should return almost
		// immediately) -- otherwise a Start for the same room_id arriving
		// right after this returns could see the stale entry and skip
		// spawning a replacement.
		<-agent.done
		return nil
	}
}

// Shutdown stops the manager itself. It does NOT stop managed agents --
// they keep running (own process group, see Start) so a worker-manager
// redeploy doesn't kill active calls. See the Manager doc for the tradeoff
// this implies (the new instance can't Stop() them by room_id anymore).
func (m *Manager) Shutdown() {
	m.mu.Lock()
	n := len(m.agents)
	m.mu.Unlock()
	logging.L.Info("workermanager: shutting down, leaving managed agents running", "agent_count", n)
}
