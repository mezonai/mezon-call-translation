// Package orchestratorclient is the agents-bot's HTTP client for forwarding
// chat messages to the orchestrator's SSE broadcast endpoint.
package orchestratorclient

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	apiKey  string
	http    *http.Client
}

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
		http:    &http.Client{Timeout: 5 * time.Second},
	}
}

// PushChatExternal forwards a meeting room chat message to orchestrator,
// which broadcasts it to all connected SSE consumers.
// POST /api/v2/agent_push_chat_external
func (c *Client) PushChatExternal(ctx context.Context, roomName, roomID, participantIdentity, message, timeStr string) error {
	payload := map[string]string{
		"room_name":            roomName,
		"room_id":              roomID,
		"participant_identity": participantIdentity,
		"message":              message,
	}
	if timeStr != "" {
		payload["time"] = timeStr
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode push_chat_external: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v2/agent_push_chat_external", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("orchestratorclient: build push_chat_external request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: push_chat_external: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("orchestratorclient: push_chat_external: HTTP %d", resp.StatusCode)
	}
	return nil
}
