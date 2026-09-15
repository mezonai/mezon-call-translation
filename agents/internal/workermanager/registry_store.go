package workermanager

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// persistedAgent is one entry in the on-disk registry -- just enough for
// Phase 3 (mezon-sfu-migration-plan.md's restart-recovery gap, not
// implemented yet) to verify a pid is still the right process before
// trusting a reconnect. Deliberately doesn't carry `role`: Phase 3 only
// needs to re-signal (SIGTERM/SIGKILL) an already-running process, never to
// re-derive anything role-dependent, and the real dispatch event doesn't
// carry role today anyway (see StartEvent's doc) -- most entries would just
// record the config default for no benefit.
type persistedAgent struct {
	RoomID      uint64    `json:"room_id"`
	PID         int       `json:"pid"`
	AgentUserID int64     `json:"agent_user_id"`
	StartedAt   time.Time `json:"started_at"`
}

type persistedRegistry struct {
	Agents []persistedAgent `json:"agents"`
}

// registryPath is the file Phase 3's startup reconciliation will read.
// Phase 2 (this file) only ever writes it.
func (m *Manager) registryPath() string {
	return filepath.Join(m.cfg.StateDir, "registry.json")
}

// persistRegistry snapshots the current in-memory registry to disk. Called
// after every mutation of m.agents (Start/Stop/reap) so the file on disk
// never lags behind by more than the time it takes to write it.
//
// Must NOT be called while holding m.mu -- it takes the lock itself, only
// briefly, to read a consistent snapshot. The *whole* function body (not
// just the map read) runs under persistMu: two persistRegistry calls can
// legitimately be triggered concurrently (Start room A and Stop room B run
// on different shards, unserialized against each other), and without a
// lock spanning the entire snapshot-then-write cycle, two failure modes are
// possible -- both real, not hypothetical, see manager.go's persistMu doc:
// (1) two writers racing the same "registry.json.tmp" path (WriteFile/
// Rename interleaving, not just "who wins" but literally reading back the
// wrong writer's own bytes before renaming them), and (2) even with that
// serialized, a call that captured an *older* map snapshot winning the race
// to actually perform its write last, regressing the file to stale data.
// persistMu spanning the whole thing makes "last to acquire the lock" and
// "last snapshot captured" the same event, which is what makes "last
// writer wins" actually correct.
//
// Best-effort: a failure here is logged, not fatal -- Phase 3 will simply
// have nothing (or a stale entry) to reconcile from on the next startup,
// which is exactly today's pre-Phase-2 behavior, not a regression.
func (m *Manager) persistRegistry() {
	m.persistMu.Lock()
	defer m.persistMu.Unlock()

	m.mu.Lock()
	reg := persistedRegistry{Agents: make([]persistedAgent, 0, len(m.agents))}
	for _, a := range m.agents {
		reg.Agents = append(reg.Agents, persistedAgent{
			RoomID:      a.roomID,
			PID:         a.process.Pid,
			AgentUserID: a.agentUserID,
			StartedAt:   a.startedAt,
		})
	}
	m.mu.Unlock()

	data, err := json.MarshalIndent(reg, "", "  ")
	if err != nil {
		logging.L.Error("workermanager: failed to marshal registry, not persisted this update", logging.ErrAttrs(err)...)
		return
	}

	path := m.registryPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		logging.L.Error("workermanager: failed to create registry dir", append(logging.ErrAttrs(err), "dir", filepath.Dir(path))...)
		return
	}

	// Write to a temp file then rename over the real path -- os.Rename is
	// atomic at the filesystem level (no reader ever sees a half-written
	// file), unlike writing registry.json in place, which a crash mid-write
	// could leave truncated/corrupt for Phase 3 to choke on later.
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		logging.L.Error("workermanager: failed to write registry tmp file", append(logging.ErrAttrs(err), "path", tmp)...)
		return
	}
	if err := os.Rename(tmp, path); err != nil {
		logging.L.Error("workermanager: failed to rename registry into place", append(logging.ErrAttrs(err), "path", path)...)
	}
}
