// Package agentsbotclient is the agent's HTTP client for the agents-bot
// service. It registers and unregisters active rooms with the gateway
// so it can filter chat messages for forwarding.
package agentsbotclient

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Client talks to the agents-bot HTTP API.
type Client struct {
	baseURL string
	http    *http.Client
}

// BotProfile is the global profile of the bot authenticated by agents-bot.
type BotProfile struct {
	Username string `json:"username"`
	Avatar   string `json:"avatar"`
}

// New creates a gateway client. baseURL is e.g. "http://localhost:8003".
// Returns nil if baseURL is empty (gateway not configured).
func New(baseURL string) *Client {
	if baseURL == "" {
		return nil
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 3 * time.Second},
	}
}

// GetBotProfile returns the username and avatar of the bot authenticated by
// agents-bot. It is intentionally separate from the user-resolution API.
func (c *Client) GetBotProfile(ctx context.Context) (*BotProfile, error) {
	if c == nil {
		return nil, fmt.Errorf("agentsbotclient: client is not configured")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/api/bot/profile", nil)
	if err != nil {
		return nil, fmt.Errorf("agentsbotclient: build get bot profile request: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("agentsbotclient: get bot profile: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		msg, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("agentsbotclient: get bot profile: HTTP %d: %s", resp.StatusCode, string(msg))
	}

	var profile BotProfile
	if err := json.NewDecoder(resp.Body).Decode(&profile); err != nil {
		return nil, fmt.Errorf("agentsbotclient: decode bot profile: %w", err)
	}
	if profile.Username == "" {
		return nil, fmt.Errorf("agentsbotclient: bot profile has empty username")
	}
	return &profile, nil
}

// RegisterRoom tells the gateway that this agent has an active room.
// The gateway uses this to filter which channel messages to forward.
func (c *Client) RegisterRoom(ctx context.Context, roomName, roomID string) error {
	if c == nil {
		return nil
	}
	body, _ := json.Marshal(map[string]string{"room_name": roomName, "room_id": roomID})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/rooms/register", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("agentsbotclient: build register request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("agentsbotclient: register_room: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		msg, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("agentsbotclient: register_room: HTTP %d: %s", resp.StatusCode, string(msg))
	}
	return nil
}

// UnregisterRoom tells the gateway this agent's room is no longer active.
// roomID protects against unregistering a newer session that reused the roomName.
func (c *Client) UnregisterRoom(ctx context.Context, roomName, roomID string) error {
	if c == nil {
		return nil
	}
	body, _ := json.Marshal(map[string]string{"room_name": roomName, "room_id": roomID})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/rooms/unregister", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("agentsbotclient: build unregister request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("agentsbotclient: unregister_room: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		msg, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("agentsbotclient: unregister_room: HTTP %d: %s", resp.StatusCode, string(msg))
	}
	return nil
}
