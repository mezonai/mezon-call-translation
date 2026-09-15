// Package sttclient streams PCM to the Vosk STT service over WebSocket and
// dispatches transcription results. Ported from the old Python agent's
// STTWebSocketClient (agents/src/core/websocket/stt_client.py), simplified:
//   - No client-side batching/rate-limiting -- each PCM chunk already
//     arrives pre-sized at ~20ms (one Opus frame) from
//     internal/audiopipeline, sent as its own WS binary frame.
//   - No circuit breaker -- mirrors this package's sibling recordclient:
//     drop and count rather than trip a breaker, see SendPCM.
//   - No auth headers -- grepping the old base_client.py, the configured
//     auth_token/api_key were never actually attached to the connection;
//     this port only carries over what's real.
package sttclient

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// Client implements audiopipeline.Sink.
type Client struct {
	conn *websocket.Conn

	queue      chan []byte
	writerDone chan struct{}
	readerDone chan struct{}
	closed     atomic.Bool
	dropped    atomic.Int64 // observability only; the Vosk protocol has no drop-report message like recordclient's DroppedFrames
}

// Dial connects and starts the write/read loops. onMessage is called from
// the read loop for every message the STT service sends (JSON or plain
// text) -- must not block.
func Dial(ctx context.Context, wsURL string, queueSize int, onMessage func(raw []byte)) (*Client, error) {
	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	conn, resp, err := dialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return nil, fmt.Errorf("sttclient: dial %s: %w", wsURL, err)
	}
	if resp != nil {
		_ = resp.Body.Close()
	}

	c := &Client{
		conn:       conn,
		queue:      make(chan []byte, queueSize),
		writerDone: make(chan struct{}),
		readerDone: make(chan struct{}),
	}

	go c.writeLoop()
	go c.readLoop(onMessage)

	return c, nil
}

// SendPCM enqueues one PCM chunk. Non-blocking: if the STT service (or the
// network to it) can't keep up, the chunk is dropped rather than blocking
// the caller (same posture as recordclient.Forwarder.SendPCM).
func (c *Client) SendPCM(pcm []byte) {
	if c.closed.Load() {
		return
	}
	select {
	case c.queue <- pcm:
	default:
		n := c.dropped.Add(1)
		if n%100 == 1 { // log occasionally, not per-frame
			logging.L.Warn("sttclient: queue full, dropping audio", "dropped_total", n)
		}
	}
}

func (c *Client) writeLoop() {
	defer close(c.writerDone)
	for chunk := range c.queue {
		if err := c.conn.WriteMessage(websocket.BinaryMessage, chunk); err != nil {
			logging.L.Warn("sttclient: write loop stopped", logging.ErrAttrs(err)...)
			return
		}
	}
}

func (c *Client) readLoop(onMessage func(raw []byte)) {
	defer close(c.readerDone)
	for {
		_, raw, err := c.conn.ReadMessage()
		if err != nil {
			logging.L.Info("sttclient: read loop stopped", logging.ErrAttrs(err)...)
			return
		}
		if onMessage != nil {
			onMessage(raw)
		}
	}
}

// Close flushes pending writes, then tears down the connection. Safe to
// call once; matches the audiopipeline.Sink contract (no error return --
// failures are logged, not propagated, since a stuck STT session must
// never hold up track/session teardown).
func (c *Client) Close() {
	if !c.closed.CompareAndSwap(false, true) {
		return
	}
	close(c.queue)

	select {
	case <-c.writerDone:
	case <-time.After(5 * time.Second):
	}
	_ = c.conn.Close()
	select {
	case <-c.readerDone:
	case <-time.After(5 * time.Second):
	}
}
