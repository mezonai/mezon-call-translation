package orchestratorclient

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

const (
	sseInitialRetryDelay = 1 * time.Second
	sseMaxRetryDelay     = 60 * time.Second
)

// RunAgentRequestListener connects to orchestrator's SSE agent-request
// stream and dispatches events to registered handlers (RegisterHandler)
// until ctx is cancelled. Auto-reconnects with exponential backoff on any
// connection error -- unlike the agent's own mezon-sfu session (see
// internal/reconnect), there's no bounded attempt limit here: this is a
// best-effort side channel (TTS trigger, transcription on/off), not
// something that should ever make the whole agent process exit just
// because orchestrator is briefly unreachable.
func (c *Client) RunAgentRequestListener(ctx context.Context, agentID, roomName string) error {
	delay := sseInitialRetryDelay

	for ctx.Err() == nil {
		err := c.runAgentRequestListenerOnce(ctx, agentID, roomName)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil {
			logging.L.Warn("orchestratorclient: sse listener error, retrying", append(logging.ErrAttrs(err), "retry_in", delay)...)
		}

		select {
		case <-time.After(delay):
		case <-ctx.Done():
			return ctx.Err()
		}
		delay *= 2
		if delay > sseMaxRetryDelay {
			delay = sseMaxRetryDelay
		}
	}
	return ctx.Err()
}

func (c *Client) runAgentRequestListenerOnce(ctx context.Context, agentID, roomName string) error {
	q := url.Values{"agent_id": {agentID}}
	if roomName != "" {
		q.Set("room_name", roomName)
	}
	reqURL := c.baseURL + "/api/v2/sse/agent-requests?" + q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return fmt.Errorf("orchestratorclient: build sse request: %w", err)
	}
	req.Header.Set("Accept", "text/event-stream")
	c.authHeader(req)

	// No client-level timeout for a streaming connection -- ctx cancellation
	// is what ends this.
	streamClient := &http.Client{}
	resp, err := streamClient.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: sse connect: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("orchestratorclient: sse connect: HTTP %d", resp.StatusCode)
	}
	logging.L.Info("orchestratorclient: sse connected", "agent_id", agentID, "room_name", roomName)

	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	var eventType string
	var dataLines []string
	flush := func() {
		if len(dataLines) == 0 && eventType == "" {
			return
		}
		c.handleSSEEvent(eventType, strings.Join(dataLines, "\n"))
		eventType = ""
		dataLines = nil
	}

	for scanner.Scan() {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		line := scanner.Text()
		switch {
		case line == "":
			flush()
		case strings.HasPrefix(line, "event:"):
			eventType = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
		case strings.HasPrefix(line, "data:"):
			dataLines = append(dataLines, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	flush()
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("orchestratorclient: sse read: %w", err)
	}
	return fmt.Errorf("orchestratorclient: sse stream closed by server")
}

func (c *Client) handleSSEEvent(eventType, data string) {
	// Mirrors the old Python listener's _process_sse_event: only these
	// three named events are special-cased and ignored; every other block
	// (including the common case of no `event:` line at all, i.e. the
	// default SSE "message" event) is treated as a potential agent request
	// and parsed as JSON below.
	switch eventType {
	case "connected", "heartbeat", "disconnect":
		return
	}
	if data == "" {
		return
	}

	var req struct {
		RequestID   string         `json:"request_id"`
		RequestType string         `json:"request_type"`
		Payload     map[string]any `json:"payload"`
	}
	if err := json.Unmarshal([]byte(data), &req); err != nil {
		logging.L.Warn("orchestratorclient: undecodable sse agent request", logging.ErrAttrs(err)...)
		return
	}

	handler, ok := c.handlers[req.RequestType]
	if !ok {
		logging.L.Warn("orchestratorclient: no handler for request type", "request_type", req.RequestType, "request_id", req.RequestID)
		return
	}
	if err := handler(req.Payload); err != nil {
		logging.L.Error("orchestratorclient: request handler failed",
			append(logging.ErrAttrs(err), "request_type", req.RequestType, "request_id", req.RequestID)...)
		return
	}
	logging.L.Info("orchestratorclient: handled sse request", "request_type", req.RequestType, "request_id", req.RequestID)
}
