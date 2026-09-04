package agentcontrol

import (
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSocketPath(t *testing.T) {
	got := SocketPath("/run/mezon-agents", 42)
	want := filepath.Join("/run/mezon-agents", "room-42.sock")
	if got != want {
		t.Errorf("SocketPath = %q, want %q", got, want)
	}
}

func TestListenCreatesDirAndSocket(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "socket-dir") // doesn't exist yet -- Listen must MkdirAll it
	ln, err := Listen(dir, 1)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	defer ln.Close()

	path := SocketPath(dir, 1)
	if _, err := os.Stat(path); err != nil {
		t.Errorf("socket file not created at %s: %v", path, err)
	}
}

func TestListenRemovesStaleSocket(t *testing.T) {
	dir := t.TempDir()

	ln1, err := Listen(dir, 7)
	if err != nil {
		t.Fatalf("first Listen: %v", err)
	}
	// Simulate a prior crash: the process dies without a clean Close, so
	// the socket file is left behind but nothing is listening on it
	// anymore. A second Listen for the same room_id must still succeed --
	// this is exactly the "agent restarts after a crash" case, not just
	// the worker-manager-restart case this package is otherwise about.
	path := ln1.Addr().String()
	_ = ln1.Close() // real Close also removes the file; recreate it to simulate the leftover
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatalf("recreate stale socket file: %v", err)
	}

	ln2, err := Listen(dir, 7)
	if err != nil {
		t.Fatalf("second Listen (stale file present): %v", err)
	}
	defer ln2.Close()
}

func TestServeAcceptsAndDrainsConnections(t *testing.T) {
	dir := t.TempDir()
	ln, err := Listen(dir, 99)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	defer ln.Close()

	go Serve(ln)

	conn, err := net.DialTimeout("unix", SocketPath(dir, 99), time.Second)
	if err != nil {
		t.Fatalf("Dial: %v", err)
	}
	// Serve should read (and discard) whatever's written, not error out or
	// close the connection from its side.
	if _, err := conn.Write([]byte("hello")); err != nil {
		t.Fatalf("write: %v", err)
	}
	_ = conn.Close()

	// Listener must still be usable for a second connection after the
	// first one closes -- Serve's Accept loop shouldn't exit just because
	// one connection ended.
	conn2, err := net.DialTimeout("unix", SocketPath(dir, 99), time.Second)
	if err != nil {
		t.Fatalf("second Dial: %v", err)
	}
	_ = conn2.Close()
}

func TestServeReturnsWhenListenerCloses(t *testing.T) {
	dir := t.TempDir()
	ln, err := Listen(dir, 5)
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}

	done := make(chan struct{})
	go func() {
		Serve(ln)
		close(done)
	}()

	_ = ln.Close()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Serve did not return after listener Close")
	}
}
