package workermanager

import (
	"net"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/agentcontrol"
)

// spawnRealProcess starts a real, short-lived process and returns it,
// killed on test cleanup -- reconcile.go's checks (processAlive,
// processIsAgentBinary, socket dial) all operate on real OS state
// (/proc, kill(pid,0), a real listener), so tests exercise them for real
// rather than mocking any of it.
func spawnRealProcess(t *testing.T) *exec.Cmd {
	t.Helper()
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatalf("spawn real process: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	return cmd
}

func sleepBinPath(t *testing.T) string {
	t.Helper()
	path, err := exec.LookPath("sleep")
	if err != nil {
		t.Skipf("sleep binary not found, skipping: %v", err)
	}
	return path
}

func TestProcessAlive(t *testing.T) {
	cmd := spawnRealProcess(t)
	if !processAlive(cmd.Process.Pid) {
		t.Error("processAlive = false for a real running process")
	}

	_ = cmd.Process.Kill()
	_, _ = cmd.Process.Wait() // reap it so the pid is actually released, not just zombied

	if processAlive(cmd.Process.Pid) {
		t.Error("processAlive = true for a process that was killed and reaped")
	}
}

func TestProcessIsAgentBinary(t *testing.T) {
	binPath := sleepBinPath(t)
	cmd := spawnRealProcess(t)

	if !processIsAgentBinary(cmd.Process.Pid, binPath) {
		t.Error("processIsAgentBinary = false comparing a real sleep process against sleep's own path")
	}
	if processIsAgentBinary(cmd.Process.Pid, "/bin/definitely-not-sleep") {
		t.Error("processIsAgentBinary = true against an unrelated path")
	}
	if processIsAgentBinary(999999999, binPath) {
		t.Error("processIsAgentBinary = true for a pid that doesn't exist")
	}
}

// testManager builds a Manager configured to use dir for both state and
// socket dir (fine for tests -- real deploy separates them, see the
// [Deploy] note in mezon-sfu-migration-plan.md, but nothing here cares).
func testManager(dir string, agentBinPath string) *Manager {
	return &Manager{
		cfg: Config{
			StateDir:     dir,
			SocketDir:    dir,
			AgentBinPath: agentBinPath,
		},
		agents: make(map[uint64]*managedAgent),
	}
}

func TestReconcileNoRegistryFile(t *testing.T) {
	dir := t.TempDir()
	m := testManager(dir, sleepBinPath(t))

	m.Reconcile() // must not panic/error just because the file doesn't exist yet

	m.mu.Lock()
	n := len(m.agents)
	m.mu.Unlock()
	if n != 0 {
		t.Errorf("got %d adopted agents from a nonexistent registry, want 0", n)
	}
}

func TestReconcileAdoptsLiveAgent(t *testing.T) {
	dir := t.TempDir()
	binPath := sleepBinPath(t)

	// Simulate "a previous worker-manager instance spawned this and wrote
	// the registry, then died" -- the real agent process, a real listening
	// control socket (as internal/agentcontrol.Listen would open at the
	// agent's own startup), and a registry.json referencing both.
	//
	// Deliberately NOT using agentcontrol.Serve here: Serve's accept loop
	// would run in *this test process*, not in cmd (the fake agent
	// process) -- killing cmd later would then do nothing to the
	// connection, since the two are unrelated OS processes. Instead we
	// keep a handle on the accepted (server-side) connection ourselves, so
	// we can close *that* to simulate the real-world trigger accurately:
	// when an agent process actually dies, the kernel closes all its file
	// descriptors, including its end of this socket -- which is what
	// makes the worker-manager side's Read() see EOF. Closing the
	// server-side conn directly reproduces that effect without needing a
	// second real subprocess just to host agentcontrol.Serve.
	cmd := spawnRealProcess(t)
	ln, err := agentcontrol.Listen(dir, 1)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	defer ln.Close()
	serverConnCh := make(chan net.Conn, 1)
	go func() {
		conn, err := ln.Accept()
		if err == nil {
			serverConnCh <- conn
		}
	}()

	writer := testManager(dir, binPath)
	writer.agents[1] = &managedAgent{
		process:     cmd.Process,
		cmd:         cmd,
		roomID:      1,
		agentUserID: 900000001,
		startedAt:   time.Now(),
		done:        make(chan struct{}),
	}
	writer.persistRegistry()

	// Now the actual thing under test: a *fresh* Manager (new instance,
	// as if worker-manager just restarted) reconciling from that file.
	m := testManager(dir, binPath)
	m.Reconcile()

	m.mu.Lock()
	adopted, ok := m.agents[1]
	m.mu.Unlock()
	if !ok {
		t.Fatal("room 1 not adopted")
	}
	if adopted.process.Pid != cmd.Process.Pid {
		t.Errorf("adopted pid = %d, want %d", adopted.process.Pid, cmd.Process.Pid)
	}
	if adopted.cmd != nil {
		t.Error("adopted agent's cmd should be nil (not a real child of this instance)")
	}
	if adopted.conn == nil {
		t.Error("adopted agent's conn should be set")
	}

	// Reconcile already started reap(adopted) itself (see reconcile.go) --
	// don't start a second one here, or both would race to close(a.done)
	// and panic. This exercises that already-running goroutine's actual
	// exit-detection path (via the socket, not cmd.Wait, which would
	// panic/block forever on a nil cmd), not a copy of it.
	var serverConn net.Conn
	select {
	case serverConn = <-serverConnCh:
	case <-time.After(3 * time.Second):
		t.Fatal("worker-manager's dial was never accepted")
	}
	_ = cmd.Process.Kill() // the real-world trigger, for realism -- see the process, not what reap actually observes
	_ = serverConn.Close() // what reap actually observes: its end of the socket closing

	select {
	case <-adopted.done:
	case <-time.After(3 * time.Second):
		t.Fatal("reap did not detect adopted agent's exit within 3s")
	}

	m.mu.Lock()
	_, stillThere := m.agents[1]
	m.mu.Unlock()
	if stillThere {
		t.Error("room 1 still in m.agents after reap should have removed it")
	}
}

func TestReconcileSkipsDeadPid(t *testing.T) {
	dir := t.TempDir()
	binPath := sleepBinPath(t)

	cmd := spawnRealProcess(t)
	deadPID := cmd.Process.Pid
	_ = cmd.Process.Kill()
	_, _ = cmd.Process.Wait() // ensure it's actually reaped, pid free to be "dead"

	writer := testManager(dir, binPath)
	writer.agents[1] = &managedAgent{
		process: &os.Process{Pid: deadPID}, roomID: 1, agentUserID: 1, done: make(chan struct{}),
	}
	writer.persistRegistry()

	m := testManager(dir, binPath)
	m.Reconcile()

	m.mu.Lock()
	n := len(m.agents)
	m.mu.Unlock()
	if n != 0 {
		t.Errorf("adopted %d agents from a dead pid, want 0", n)
	}
}

func TestReconcileSkipsPidReusedByDifferentBinary(t *testing.T) {
	dir := t.TempDir()
	realAgentBinPath := "/bin/definitely-not-the-real-agent-binary"

	// A real, live process -- just not running the configured agent
	// binary, simulating the pid having been recycled for something else
	// since the registry entry was written.
	cmd := spawnRealProcess(t)
	ln, err := agentcontrol.Listen(dir, 1)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	defer ln.Close()
	go agentcontrol.Serve(ln)

	writer := testManager(dir, realAgentBinPath)
	writer.agents[1] = &managedAgent{
		process: cmd.Process, cmd: cmd, roomID: 1, agentUserID: 1, done: make(chan struct{}),
	}
	writer.persistRegistry()

	m := testManager(dir, realAgentBinPath)
	m.Reconcile()

	m.mu.Lock()
	n := len(m.agents)
	m.mu.Unlock()
	if n != 0 {
		t.Errorf("adopted %d agents despite binary mismatch, want 0", n)
	}
}

func TestReconcileSkipsUnreachableSocket(t *testing.T) {
	dir := t.TempDir()
	binPath := sleepBinPath(t)

	// Live process, correct binary, but nobody listening on its control
	// socket -- e.g. the agent is between "process started" and "Listen
	// called" (a narrow window in cmd/agent's real startup order), or the
	// socket file/dir got cleaned up from under it.
	cmd := spawnRealProcess(t)

	writer := testManager(dir, binPath)
	writer.agents[1] = &managedAgent{
		process: cmd.Process, cmd: cmd, roomID: 1, agentUserID: 1, done: make(chan struct{}),
	}
	writer.persistRegistry()

	m := testManager(dir, binPath)
	m.Reconcile()

	m.mu.Lock()
	n := len(m.agents)
	m.mu.Unlock()
	if n != 0 {
		t.Errorf("adopted %d agents with no socket listening, want 0", n)
	}
}

func TestReconcileRewritesRegistryDroppingStaleEntries(t *testing.T) {
	dir := t.TempDir()
	binPath := sleepBinPath(t)

	live := spawnRealProcess(t)
	ln, err := agentcontrol.Listen(dir, 1)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	defer ln.Close()
	go agentcontrol.Serve(ln)

	dead := spawnRealProcess(t)
	deadPID := dead.Process.Pid
	_ = dead.Process.Kill()
	_, _ = dead.Process.Wait()

	writer := testManager(dir, binPath)
	writer.agents[1] = &managedAgent{process: live.Process, cmd: live, roomID: 1, agentUserID: 1, done: make(chan struct{})}
	writer.agents[2] = &managedAgent{process: &os.Process{Pid: deadPID}, roomID: 2, agentUserID: 1, done: make(chan struct{})}
	writer.persistRegistry()

	m := testManager(dir, binPath)
	m.Reconcile()

	reg := readRegistry(t, m.registryPath())
	if len(reg.Agents) != 1 || reg.Agents[0].RoomID != 1 {
		t.Fatalf("expected only room 1 to survive reconciliation in the rewritten file, got %+v", reg.Agents)
	}
}
