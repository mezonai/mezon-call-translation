package workermanager

import (
	"fmt"
	"net"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/config"
	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

type managedAgent struct {
	// process is always set, for both variants below -- Signal()/Kill() work
	// through an *os.Process regardless of whether it's a real child of this
	// process (os.FindProcess never actually starts anything on Unix, it
	// just wraps a pid for signaling).
	process *os.Process

	// cmd is set only for an agent this instance itself spawned (Start) --
	// nil for one adopted from a previous instance's registry (Phase 3,
	// reconcile.go). reap branches on this: cmd.Wait() only works for a
	// real child of the calling process, which an adopted agent no longer
	// is (see reap's doc).
	cmd *exec.Cmd

	// conn is the reverse of cmd: nil for a freshly spawned agent, set for
	// an adopted one -- the dialed connection to the agent's control socket
	// (internal/agentcontrol), used in place of cmd.Wait() to detect exit.
	conn net.Conn

	roomID      uint64
	agentUserID int64
	startedAt   time.Time
	done        chan struct{} // closed once reap's wait (cmd.Wait or conn read) returns
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

	// persistMu serializes persistRegistry's whole snapshot-then-write cycle
	// (registry_store.go) -- separate from mu (which only ever needs to be
	// held for a quick map read/write) because two persistRegistry calls
	// from different shards can legitimately run concurrently (Start room A
	// and Stop room B don't serialize against each other, only same-room
	// events do -- shard.go). Without this, two concurrent calls could both
	// target the same "registry.json.tmp" path and race each other's
	// WriteFile/Rename, or -- even single-writer-at-a-time but
	// snapshot-then-write not treated as one atomic step -- have the call
	// that captured an *older* snapshot win the race to actually write
	// last, silently regressing the file. Holding this for the entire
	// snapshot+write body makes "last to acquire the lock" and "last
	// snapshot captured" the same thing, which is what makes "last writer
	// wins" actually correct instead of just atomic-looking.
	persistMu sync.Mutex

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
			"room_id", ev.RoomID, "pid", existing.process.Pid)
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
	//
	// Pdeathsig: PR_SET_PDEATHSIG (SIGUSR1) -- the kernel delivers this to
	// the agent the instant *this* worker-manager process dies (crash,
	// kill, restart), no polling needed. Independent of Setpgid above (one
	// controls signal propagation from a terminal, the other is
	// parent-death notification) -- the two don't interact. Phase 1 of
	// mezon-sfu-migration-plan.md's restart-recovery gap; see
	// internal/agentcontrol's package doc for the rest of the design.
	// SIGUSR1 is otherwise unused anywhere in agents/ (grepped to confirm),
	// so this can't collide with the agent's own SIGINT/SIGTERM shutdown
	// handling.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGUSR1}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("workermanager: spawn agent for room_id=%d: %w", ev.RoomID, err)
	}

	agent := &managedAgent{
		process:     cmd.Process,
		cmd:         cmd,
		roomID:      ev.RoomID,
		agentUserID: agentUserID,
		startedAt:   time.Now(),
		done:        make(chan struct{}),
	}

	m.mu.Lock()
	m.agents[ev.RoomID] = agent
	m.mu.Unlock()
	m.persistRegistry()

	logging.L.Info("workermanager: spawned agent",
		"room_id", ev.RoomID, "agent_user_id", agentUserID, "role", role, "pid", cmd.Process.Pid)

	go m.reap(agent)
	return nil
}

// reap waits for an agent to exit and removes it from the registry. This is
// what makes an agent that exhausts its own reconnect budget (see
// internal/reconnect) or otherwise exits on its own clean itself out of
// this manager's bookkeeping, not just processes we explicitly Stop().
//
// Two ways to wait, depending on how this agent ended up in m.agents:
//   - Spawned by this instance (a.cmd != nil): a.cmd.Wait() -- the normal
//     case, this process really is our child.
//   - Adopted from a previous instance's registry (reconcile.go, a.conn !=
//     nil): no longer a real child of *this* process -- it was reparented
//     to init/a subreaper when the worker-manager that originally spawned
//     it died, so only that reparent target can wait4() it, not us.
//     Instead, block reading on the control-socket connection: the agent's
//     own agentcontrol.Serve holds its end open and never writes anything,
//     so our Read only ever returns once the agent process exits and the
//     kernel tears down its end of the socket (EOF) -- functionally the
//     same "block until it's really gone" property as cmd.Wait(), just
//     sourced from a socket instead of the process table.
func (m *Manager) reap(a *managedAgent) {
	var err error
	if a.cmd != nil {
		err = a.cmd.Wait()
	} else {
		_, err = a.conn.Read(make([]byte, 1))
		_ = a.conn.Close()
	}
	close(a.done)

	m.mu.Lock()
	if cur, ok := m.agents[a.roomID]; ok && cur == a {
		delete(m.agents, a.roomID)
	}
	m.mu.Unlock()
	m.persistRegistry()

	logging.L.Info("workermanager: agent process exited",
		append(logging.ErrAttrs(err), "room_id", a.roomID, "pid", a.process.Pid, "uptime", time.Since(a.startedAt))...)
}

// Stop signals the agent for ev.RoomID to shut down: SIGTERM first (the
// agent's own signal handler treats this as "leave the room", see
// cmd/agent/main.go), escalating to SIGKILL in the background if it hasn't
// exited within cfg.StopTimeout. A room with no running agent is logged and
// treated as success (already in the desired end state).
//
// The room is removed from the registry here, synchronously, rather than
// waiting for the process to actually exit (reap()) -- deliberately, see
// [2026-08-21] below. Stop itself therefore returns as soon as SIGTERM is
// sent, without waiting out cfg.StopTimeout; the wait-then-maybe-SIGKILL
// part runs in its own goroutine (killAfterTimeout) so it can take as long
// as it needs without blocking anything else.
//
// [2026-08-21] Removing the room from the registry only used to happen in
// reap(), which meant Stop() had to block the calling goroutine until the
// process actually exited (waiting out the full SIGTERM grace period, then
// SIGKILL) -- otherwise a Start for the same room_id could see the stale
// entry and skip spawning a replacement (Start's "already running?" check).
// The problem: dispatch.go/shard.go route every event for a given room_id
// through the *same* shard goroutine, specifically so start/stop for one
// room can never race or reorder -- which also means a Start queued right
// behind a Stop for the same room_id had to wait for that same blocking
// Stop to finish first. Concretely: someone leaves a room (agent gets a
// stop event), decides to add it back a few seconds later while the old
// agent's still mid-graceful-shutdown (flushing its record-service forwarder,
// reporting to orchestrator, etc, agents/internal/rtcagent's trackWG/
// ttsplayer close grace) -- the UI already shows no agent (mezon-sfu treats
// the WS close as an immediate leave, cmd/agent/main.go's ctx cancellation
// closes it right away, well before any of that local cleanup finishes) but
// the "add" click would silently queue behind the still-running Stop call
// and only actually spawn once the old process's cleanup either finished or
// got SIGKILLed at cfg.StopTimeout. Freeing the room here instead means a
// same-room Start queued right after a Stop can proceed immediately -- the
// old process's local cleanup (and, if it overruns, its eventual SIGKILL)
// keeps running independently in the background, unobserved by anything
// that cares whether the room has an agent. See reap()'s `cur == a` guard:
// it already anticipated exactly this (a stale managedAgent being reaped
// after a newer one has taken its place in the registry), so this wasn't a
// new invariant to introduce, just wiring Stop up to rely on it too.
func (m *Manager) Stop(ev StopEvent) error {
	if ev.RoomID == 0 {
		return fmt.Errorf("workermanager: stop event missing room_id")
	}

	m.mu.Lock()
	agent, ok := m.agents[ev.RoomID]
	if ok {
		delete(m.agents, ev.RoomID)
	}
	m.mu.Unlock()
	if !ok {
		logging.L.Warn("workermanager: stop ignored, no agent running for room", "room_id", ev.RoomID)
		return nil
	}
	m.persistRegistry()

	logging.L.Info("workermanager: stopping agent", "room_id", ev.RoomID, "pid", agent.process.Pid)
	if err := agent.process.Signal(syscall.SIGTERM); err != nil {
		return fmt.Errorf("workermanager: sigterm room_id=%d pid=%d: %w", ev.RoomID, agent.process.Pid, err)
	}

	go m.killAfterTimeout(agent, ev.RoomID)
	return nil
}

// killAfterTimeout waits for agent to exit on its own after Stop's SIGTERM,
// escalating to SIGKILL if it overruns cfg.StopTimeout. Runs detached from
// Stop's caller (see Stop's doc) -- errors here have no caller left to
// return to, so they're logged directly instead.
func (m *Manager) killAfterTimeout(agent *managedAgent, roomID uint64) {
	select {
	case <-agent.done:
		return
	case <-time.After(m.cfg.StopTimeout):
		logging.L.Warn("workermanager: agent did not exit within stop timeout, sending SIGKILL",
			"room_id", roomID, "pid", agent.process.Pid, "timeout", m.cfg.StopTimeout)
		if err := agent.process.Kill(); err != nil {
			logging.L.Error("workermanager: sigkill failed",
				append(logging.ErrAttrs(err), "room_id", roomID, "pid", agent.process.Pid)...)
			return
		}
		<-agent.done
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
