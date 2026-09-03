package workermanager

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// fakeAgent starts a real (trivially short-lived) process so managedAgent's
// cmd.Process.Pid -- which persistRegistry reads -- is a genuine pid, same
// as it would be for a real spawned agent. Killed on test cleanup.
func fakeAgent(t *testing.T, roomID uint64, agentUserID int64) *managedAgent {
	t.Helper()
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatalf("spawn fake agent process: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	return &managedAgent{
		process:     cmd.Process,
		cmd:         cmd,
		roomID:      roomID,
		agentUserID: agentUserID,
		startedAt:   time.Now(),
		done:        make(chan struct{}),
	}
}

func readRegistry(t *testing.T, path string) persistedRegistry {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read registry: %v", err)
	}
	var reg persistedRegistry
	if err := json.Unmarshal(data, &reg); err != nil {
		t.Fatalf("unmarshal registry: %v", err)
	}
	return reg
}

func TestPersistRegistryWritesCurrentAgents(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "state-dir") // doesn't exist yet
	m := &Manager{cfg: Config{StateDir: dir}, agents: make(map[uint64]*managedAgent)}

	a1 := fakeAgent(t, 1, 900000001)
	a2 := fakeAgent(t, 2, 900000001)
	m.agents[1] = a1
	m.agents[2] = a2

	m.persistRegistry()

	reg := readRegistry(t, m.registryPath())
	if len(reg.Agents) != 2 {
		t.Fatalf("got %d agents, want 2: %+v", len(reg.Agents), reg.Agents)
	}
	byRoom := make(map[uint64]persistedAgent)
	for _, a := range reg.Agents {
		byRoom[a.RoomID] = a
	}
	if got := byRoom[1].PID; got != a1.cmd.Process.Pid {
		t.Errorf("room 1 pid = %d, want %d", got, a1.cmd.Process.Pid)
	}
	if got := byRoom[2].PID; got != a2.cmd.Process.Pid {
		t.Errorf("room 2 pid = %d, want %d", got, a2.cmd.Process.Pid)
	}
}

func TestPersistRegistryReplacesNotMerges(t *testing.T) {
	dir := t.TempDir()
	m := &Manager{cfg: Config{StateDir: dir}, agents: make(map[uint64]*managedAgent)}

	m.agents[1] = fakeAgent(t, 1, 900000001)
	m.persistRegistry()

	// Room 1 "stops" (removed from the in-memory map, as Stop()/reap() do
	// before calling persistRegistry) and room 2 starts instead.
	delete(m.agents, 1)
	m.agents[2] = fakeAgent(t, 2, 900000001)
	m.persistRegistry()

	reg := readRegistry(t, m.registryPath())
	if len(reg.Agents) != 1 || reg.Agents[0].RoomID != 2 {
		t.Fatalf("expected only room 2 after replace, got %+v", reg.Agents)
	}
}

// TestPersistRegistryConcurrentCallsNeverCorruptFile fires many concurrent
// persistRegistry calls -- mimicking Start/Stop/reap for different rooms
// hitting it at once from different shards, unserialized against each
// other by design (shard.go) -- and checks the file left behind is always
// well-formed and matches *some* real state that existed at some point,
// never a torn/interleaved write from two writers racing the same
// "registry.json.tmp" path. Regression guard for persistMu (manager.go):
// remove that lock and this starts failing/flaking under `go test -race`
// on WriteFile/Rename touching the shared tmp path from multiple
// goroutines, and occasionally leaves an empty or truncated registry.json.
func TestPersistRegistryConcurrentCallsNeverCorruptFile(t *testing.T) {
	dir := t.TempDir()
	m := &Manager{cfg: Config{StateDir: dir}, agents: make(map[uint64]*managedAgent)}

	const n = 20
	agents := make([]*managedAgent, n)
	for i := range agents {
		agents[i] = fakeAgent(t, uint64(i+1), 900000001)
	}

	var wg sync.WaitGroup
	for i := range agents {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			// Each goroutine adds its own room then persists -- same shape
			// as Start()'s "mutate map under m.mu, unlock, then persist"
			// sequence, run concurrently across "shards".
			m.mu.Lock()
			m.agents[agents[i].roomID] = agents[i]
			m.mu.Unlock()
			m.persistRegistry()
		}(i)
	}
	wg.Wait()

	// The file must parse cleanly (never half-written) and, since every
	// goroutine only ever adds and nothing removes, the very last persist
	// to actually run must have seen all n agents -- if any call captured
	// a stale/partial snapshot and still won the write race, this count
	// would be less than n.
	reg := readRegistry(t, m.registryPath())
	if len(reg.Agents) != n {
		t.Fatalf("got %d agents in final registry, want %d (torn/stale write) -- entries: %+v", len(reg.Agents), n, reg.Agents)
	}
}

func TestPersistRegistryEmptyWhenNoAgents(t *testing.T) {
	dir := t.TempDir()
	m := &Manager{cfg: Config{StateDir: dir}, agents: make(map[uint64]*managedAgent)}

	m.persistRegistry()

	reg := readRegistry(t, m.registryPath())
	if len(reg.Agents) != 0 {
		t.Fatalf("expected empty registry, got %+v", reg.Agents)
	}
}
