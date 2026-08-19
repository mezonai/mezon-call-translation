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
//
// [NOTE 2026-08-19, chưa triển khai, cần suy nghĩ thêm] Sketch cho hướng
// reconciliation nếu làm: ghi 1 file nhỏ {room_id, pid, agent_user_id,
// started_at} khi spawn (xoá khi reap/Stop xong); lúc start lại, scan thư
// mục đó, check từng pid còn sống không (kill(pid,0)) VÀ đúng là tiến trình
// `agent` thật (đối chiếu /proc/<pid>/exe hoặc cmdline, tránh nhầm pid đã bị
// OS tái sử dụng cho process khác) rồi mới nạp lại vào `agents`. Điểm khó:
// agent con dùng Setpgid:true nên khi worker-manager cũ chết, nó bị
// reparent lên init -- không còn là con của instance mới, nên cmd.Wait()
// (cách reap() đang dùng) không dùng lại được cho các agent "nạp lại" này;
// phải tách 2 đường -- agent tự Start() trong đời process hiện tại vẫn
// dùng cmd.Wait() như cũ, còn agent adopt lại từ file phải tự poll
// kill(pid,0) định kỳ (đơn giản, tốn 1 goroutine/agent) hoặc dùng Linux
// pidfd_open (qua golang.org/x/sys/unix) để poll không cần sleep-loop --
// sạch hơn nhưng thêm dependency Linux-specific, có thể bắt đầu bằng bản
// polling đơn giản trước.
type Manager struct {
	cfg Config

	mu     sync.Mutex
	agents map[uint64]*managedAgent

	// shards: see shard.go's doc -- routes Start/Stop calls so events for
	// the same room always serialize with each other but never block a
	// different room's events.
	shards [numShards]chan func()
}

func New(cfg Config) *Manager {
	m := &Manager{cfg: cfg, agents: make(map[uint64]*managedAgent)}
	m.startShards()
	return m
}

// Start spawns one `agent` subprocess for ev.RoomID. Idempotent: a start
// for a room that already has a running agent is logged and ignored rather
// than double-spawning (a duplicate/retried NATS delivery shouldn't produce
// two agents fighting over the same room). The existence check below is
// safe against a concurrent Start/Stop for the *same* room_id only because
// callers always go through Manager.dispatch (shard.go), which guarantees
// at most one goroutine is ever running Start/Stop for a given room_id at a
// time -- calling Start directly from multiple goroutines for the same room
// would race between the check and the map insert below.
func (m *Manager) Start(ev StartEvent) error {
	if ev.RoomID == 0 {
		return fmt.Errorf("workermanager: start event missing room_id")
	}
	// AgentUserID always comes from config, never the event -- see
	// StartEvent's doc for why. 0 (unconfigured) is a hard error rather
	// than silently joining with a guessed id.
	if m.cfg.AgentUserIDBase == 0 {
		return fmt.Errorf("workermanager: AGENT_USER_ID_BASE not configured, cannot start room_id=%d", ev.RoomID)
	}
	agentUserID := m.cfg.AgentUserIDBase

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
		fmt.Sprintf("AGENT_USER_ID=%d", agentUserID),
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
		agentUserID: agentUserID,
		startedAt:   time.Now(),
		done:        make(chan struct{}),
	}

	m.mu.Lock()
	m.agents[ev.RoomID] = agent
	m.mu.Unlock()

	logging.L.Info("workermanager: spawned agent",
		"room_id", ev.RoomID, "agent_user_id", agentUserID, "role", role, "pid", cmd.Process.Pid)

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
