// Package agentcontrol implements the local control channel between
// worker-manager and a spawned agent subprocess, built to survive a
// worker-manager restart without losing track of already-running agents
// (mezon-sfu-migration-plan.md gap "Chưa làm, biết trước là gap" -- the
// 4-phase design there). This file is Phase 1 only:
//
//   - The agent opens this socket right at startup, not lazily -- a
//     worker-manager that restarts later must be able to dial back in
//     immediately, without the agent needing to notice or react to
//     anything first (see cmd/agent's use of Listen/Serve).
//   - Pdeathsig (set on the agent's SysProcAttr in
//     internal/workermanager/manager.go's Start) is the other half of
//     Phase 1: it tells the agent, via the kernel, the instant its parent
//     died -- no polling. cmd/agent only logs on that signal for now
//     (observability); it does not change behavior, since the socket
//     being open from the start means the agent is always reconnectable
//     regardless of whether it has "noticed" yet.
//
// Phase 2 (persisting {room_id, pid} to disk) and Phase 3 (worker-manager
// reading that file on its own startup, verifying the pid, and Dialing
// SocketPath to adopt the agent back into its registry) are not
// implemented yet -- this package only defines the shared path convention
// and the agent's listening half so both sides will agree on where the
// socket lives once Phase 3 lands, without duplicating the naming logic.
package agentcontrol

import (
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
)

// SocketPath returns the well-known control-socket path for one room's
// agent, given the directory both worker-manager and the agent are
// configured with (AGENT_SOCKET_DIR, see internal/config and
// internal/workermanager/config.go's agentPassthroughEnvKeys -- the two
// must agree on this value or Phase 3's Dial will never find the socket).
//
// Keyed by room_id, not pid: the whole point is letting a *new*
// worker-manager process (different pid than whoever originally spawned
// this agent) find the same agent after a restart without needing to know
// its old pid up front. The pid is used elsewhere (Phase 2's persisted
// registry) for identity *verification* before trusting a reconnect, not
// for addressing the socket itself.
func SocketPath(dir string, roomID uint64) string {
	return filepath.Join(dir, fmt.Sprintf("room-%d.sock", roomID))
}

// Listen opens the control socket for roomID under dir, creating dir if
// needed and removing a stale socket file left behind by a previous run of
// this same agent (e.g. a prior crash that didn't reach a clean Close --
// Unix sockets don't self-clean, so binding over a leftover file fails
// with "address already in use" otherwise). Safe to call unconditionally:
// nothing else should be listening on this exact room's socket path at the
// same time (worker-manager's own Start is idempotent per room_id, so at
// most one agent is ever running for a given room).
//
// Call this once at startup, not lazily on first use -- see the package
// doc for why.
func Listen(dir string, roomID uint64) (net.Listener, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("agentcontrol: create socket dir %s: %w", dir, err)
	}
	path := SocketPath(dir, roomID)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("agentcontrol: remove stale socket %s: %w", path, err)
	}
	ln, err := net.Listen("unix", path)
	if err != nil {
		return nil, fmt.Errorf("agentcontrol: listen %s: %w", path, err)
	}
	return ln, nil
}

// Serve accepts connections on ln until it's closed (typically via a
// deferred Close in main -- net.UnixListener.Close removes the underlying
// socket file itself, so callers don't need a separate os.Remove on
// shutdown). Meant to run in its own goroutine; returns once Accept starts
// erroring, which is the normal/expected outcome of the listener closing.
//
// Each accepted connection is just held open (blocked reading, discarding
// whatever arrives) until the peer closes it -- Phase 1 has nothing to say
// back yet. A live connection is itself the whole signal Phase 3's
// worker-manager needs ("this agent is still here"), and the connection
// dying is what Phase 4 will use in place of cmd.Wait() for an adopted
// agent (a real child's exit; an adopted one's parent link is severed, so
// Wait doesn't apply -- see manager.go's doc on that).
func Serve(ln net.Listener) {
	for {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		go func() {
			defer conn.Close()
			_, _ = io.Copy(io.Discard, conn)
		}()
	}
}
