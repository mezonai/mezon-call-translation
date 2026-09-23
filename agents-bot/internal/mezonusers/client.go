// Package mezonusers calls the Mezon batch user lookup API.
package mezonusers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	batchUsersPath   = "/dashboard/users"
	defaultTimeout   = 3 * time.Second
	maxErrorBodySize = 4 << 10
)

// UserInfo is the user profile returned by the Mezon API.
type UserInfo struct {
	UserID      string `json:"user_id"`
	UserName    string `json:"user_name"`
	DisplayName string `json:"display_name"`
	ClanNick    string `json:"clan_nick"`
}

type batchUserLookupRequest struct {
	RoomName string   `json:"room_name"`
	UserIDs  []string `json:"user_ids"`
}

type batchUserLookupResponse struct {
	Success bool       `json:"success"`
	Users   []UserInfo `json:"users"`
}

// HTTPError reports a non-success response from the Mezon API.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	if e.Body == "" {
		return fmt.Sprintf("mezon users: HTTP %d", e.StatusCode)
	}
	return fmt.Sprintf("mezon users: HTTP %d: %s", e.StatusCode, e.Body)
}

// Client performs batch Mezon user lookups.
type Client struct {
	usersEndpoint string
	bearerToken   string
	httpClient    *http.Client
}

// New creates a Mezon users client. bearerToken may be empty when the
// deployment authenticates the endpoint through another mechanism.
func New(baseURL, bearerToken string, timeout time.Duration) (*Client, error) {
	baseURL = strings.TrimSpace(baseURL)
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("mezon users: invalid base URL %q", baseURL)
	}

	if timeout <= 0 {
		timeout = defaultTimeout
	}

	return &Client{
		usersEndpoint: strings.TrimRight(baseURL, "/") + batchUsersPath,
		bearerToken:   strings.TrimSpace(bearerToken),
		httpClient:    &http.Client{Timeout: timeout},
	}, nil
}

// GetUsers resolves all requested user IDs in one Mezon API request.
func (c *Client) GetUsers(ctx context.Context, roomName string, userIDs []string) ([]UserInfo, error) {
	roomName = strings.TrimSpace(roomName)
	if roomName == "" {
		return nil, errors.New("mezon users: room_name is required")
	}
	if len(userIDs) == 0 {
		return nil, errors.New("mezon users: user_ids is required")
	}

	payload, err := json.Marshal(batchUserLookupRequest{RoomName: roomName, UserIDs: userIDs})
	if err != nil {
		return nil, fmt.Errorf("mezon users: encode request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.usersEndpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("mezon users: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	if c.bearerToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.bearerToken)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("mezon users: request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, maxErrorBodySize))
		return nil, &HTTPError{
			StatusCode: resp.StatusCode,
			Body:       strings.TrimSpace(string(body)),
		}
	}

	var batchResponse batchUserLookupResponse
	if err := json.NewDecoder(resp.Body).Decode(&batchResponse); err != nil {
		return nil, fmt.Errorf("mezon users: decode response: %w", err)
	}
	if !batchResponse.Success {
		return nil, errors.New("mezon users: unsuccessful response")
	}
	return batchResponse.Users, nil
}
