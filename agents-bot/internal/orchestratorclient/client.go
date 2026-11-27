// Package orchestratorclient is the agents-bot's HTTP client for forwarding
// chat messages to the orchestrator's SSE broadcast endpoint.
package orchestratorclient

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var ErrUnauthorized = errors.New("orchestratorclient: unauthorized")

type Client struct {
	baseURL    string
	apiKey     string
	http       *http.Client
	streamHTTP *http.Client
}

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
		http:    &http.Client{Timeout: 5 * time.Second},
		// SSE responses intentionally have no overall request timeout. The
		// stream ends through context cancellation or a server disconnect.
		streamHTTP: &http.Client{},
	}
}

type BotSession struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	ExpiresIn    int    `json:"expires_in"`
}

type MetadataEvent struct {
	EventID   string `json:"event_id"`
	EventType string `json:"event_type"`
	Room      struct {
		RoomID   string `json:"room_id"`
		RoomName string `json:"room_name"`
	} `json:"room"`
}

type MeetingSummary struct {
	Summary     string              `json:"summary"`
	ActionItems map[string][]string `json:"action_items"`
	Detail      []string            `json:"detail"`
}

type RoomSummary struct {
	RoomID      string         `json:"room_id"`
	RoomName    string         `json:"room_name"`
	SummaryData MeetingSummary `json:"summary_data"`
}

type summaryDetailResponse struct {
	Status string      `json:"status"`
	Data   RoomSummary `json:"data"`
}

type Participant struct {
	Identity string `json:"identity"`
	Name     string `json:"name"`
	State    string `json:"state"`
	JoinedAt int64  `json:"joined_at"`
}

type participantListResponse struct {
	Status       string        `json:"status"`
	Participants []Participant `json:"participants"`
}

// AuthenticateBot exchanges the configured Mezon bot credentials for an
// orchestrator JWT. The JWT authorizes metadata SSE and room-summary reads;
// it is distinct from the Mezon session token used by SendSystemMessage.
func (c *Client) AuthenticateBot(ctx context.Context, appID, token string) (*BotSession, error) {
	body, err := json.Marshal(map[string]any{
		"account": map[string]string{
			"appid": appID,
			"token": token,
		},
	})
	if err != nil {
		return nil, fmt.Errorf("orchestratorclient: encode bot login: %w", err)
	}

	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/api/v2/mezon/bot/login",
		strings.NewReader(string(body)),
	)
	if err != nil {
		return nil, fmt.Errorf("orchestratorclient: build bot login request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("orchestratorclient: bot login: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("%w: bot login HTTP %d", ErrUnauthorized, resp.StatusCode)
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("orchestratorclient: bot login: HTTP %d", resp.StatusCode)
	}

	var session BotSession
	if err := json.NewDecoder(resp.Body).Decode(&session); err != nil {
		return nil, fmt.Errorf("orchestratorclient: decode bot login: %w", err)
	}
	if session.AccessToken == "" {
		return nil, fmt.Errorf("orchestratorclient: bot login returned an empty access token")
	}
	return &session, nil
}

// StreamMetadata consumes orchestrator metadata SSE until the connection is
// closed, the handler asks to stop, or ctx is cancelled.
func (c *Client) StreamMetadata(
	ctx context.Context,
	accessToken string,
	handler func(MetadataEvent) error,
) error {
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodGet,
		c.baseURL+"/api/v2/sse/metadata",
		nil,
	)
	if err != nil {
		return fmt.Errorf("orchestratorclient: build metadata SSE request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("Accept", "text/event-stream")

	resp, err := c.streamHTTP.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: metadata SSE: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return fmt.Errorf("%w: metadata SSE HTTP %d", ErrUnauthorized, resp.StatusCode)
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("orchestratorclient: metadata SSE: HTTP %d", resp.StatusCode)
	}

	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	var data strings.Builder

	dispatch := func() error {
		raw := strings.TrimSpace(data.String())
		data.Reset()
		if raw == "" || raw == "ping" {
			return nil
		}

		var event MetadataEvent
		if err := json.Unmarshal([]byte(raw), &event); err != nil {
			return fmt.Errorf("orchestratorclient: decode metadata SSE event: %w", err)
		}
		// The initial SSE `connected` event has no lifecycle event_type.
		if event.EventType == "" {
			return nil
		}
		return handler(event)
	}

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := dispatch(); err != nil {
				return err
			}
			continue
		}
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		if data.Len() > 0 {
			data.WriteByte('\n')
		}
		data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("orchestratorclient: read metadata SSE: %w", err)
	}
	if err := dispatch(); err != nil {
		return err
	}
	return fmt.Errorf("orchestratorclient: metadata SSE closed")
}

func (c *Client) GetRoomSummary(ctx context.Context, accessToken, roomID string) (*RoomSummary, error) {
	var result summaryDetailResponse
	if err := c.getJSON(
		ctx,
		accessToken,
		"/api/v2/summary/room/id/"+url.PathEscape(roomID),
		&result,
	); err != nil {
		return nil, fmt.Errorf("orchestratorclient: get room summary: %w", err)
	}
	if result.Data.RoomID == "" {
		return nil, fmt.Errorf("orchestratorclient: summary not found for room %s", roomID)
	}
	return &result.Data, nil
}

func (c *Client) GetRoomParticipants(ctx context.Context, accessToken, roomID string) ([]Participant, error) {
	var result participantListResponse
	if err := c.getJSON(
		ctx,
		accessToken,
		"/api/v2/rooms/participant/"+url.PathEscape(roomID),
		&result,
	); err != nil {
		return nil, fmt.Errorf("orchestratorclient: get room participants: %w", err)
	}
	return result.Participants, nil
}

func (c *Client) getJSON(ctx context.Context, accessToken, path string, result any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return fmt.Errorf("%w: HTTP %d", ErrUnauthorized, resp.StatusCode)
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(result); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}
	return nil
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
