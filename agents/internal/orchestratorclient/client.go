// Package orchestratorclient talks to orchestrator_service for room and
// participant lifecycle operations.
//   - register_room / unregister_room: kept, unchanged wire contract. Not
//     optional bookkeeping -- see RegisterRoom's doc for why skipping this
//     silently drops every recording event record-service reports for this
//     session (this was originally left unported, then found to be a real
//     gap and added back on 2026-08-18, see mezon-sfu-migration-plan.md).
//   - participant_snapshot / participant_joined: report each Mezon-SFU peer's
//     stable user ID and SFU metadata display name; orchestrator persists both
//     directly in the room roster.
//
// NOT ported (out of scope for this pass, see mezon-sfu-migration-plan.md
// 2.5/2.6): push_event_session_started/ended -- checked against the old
// Python agent's own codebase, these were dead code there too (defined on
// OrchestratorClient, never called from main.go/lifecycle_manager.py), and
// orchestrator_service's register_room/unregister_room already push the
// equivalent room_started/room_ended events themselves as a side effect
// (room_registry_api.py) -- an explicit call here would just be a
// duplicate.
//
// room_name vs room_id, and why both still appear below: the old contract
// had two distinct identifiers -- room_name (LiveKit room name / Mezon
// meeting channel, reusable across many calls over time) and room_id
// (orchestrator's own stable UUID for *this* call, minted once via
// RegisterRoom and never reused). mezon-sfu only gives this agent a numeric
// room_id of its own, which is not the same thing as either -- it's used
// as room_name everywhere below (an assumption, not a confirmed contract,
// see cmd/agent's callers), while the orchestrator-minted UUID from
// RegisterRoom is used as room_id wherever the old contract wanted one
// (record-service's SessionMeta.RoomID; see RegisterRoom's doc).
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

func (c *Client) authHeader(req *http.Request) {
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
}

// RegisterRoom registers roomID (a fresh UUID the caller mints once per
// session, see cmd/agent's registerRoomWithOrchestrator) as this session's
// stable identity for roomName with orchestrator_service, and creates that
// session's Postgres room row -- POST /api/v2/room-registry/register.
//
// This is not optional bookkeeping: record-service reports recording
// lifecycle events (recording.started/.completed/.failed) to orchestrator
// using whatever room_id this agent handed it in SessionStart
// (internal/recordclient.SessionMeta.RoomID). orchestrator resolves that
// value either as a direct Postgres room_id (the happy path, which only
// exists once RegisterRoom has run) or, failing that, as a room_name to
// look up in its Redis name->id cache (which RegisterRoom also populates,
// as a fallback path) -- skip both and every recording event for this
// session gets silently dropped ("room_not_registered"), the room's
// Postgres status never leaves "pending", and no summary ever gets
// generated for the call. Call this before joining mezon-sfu (matches the
// old Python agent's ordering in main.go: room_id must be in hand before
// any track can possibly start recording).
func (c *Client) RegisterRoom(ctx context.Context, roomName, roomID string) error {
	body, err := json.Marshal(map[string]string{"room_name": roomName, "room_id": roomID})
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode register_room body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v2/room-registry/register", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("orchestratorclient: build register_room request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: register_room: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("orchestratorclient: register_room: HTTP %d", resp.StatusCode)
	}
	return nil
}

type Participant struct {
	ParticipantIdentity string `json:"participant_identity"`
	Username            string `json:"username"`
}

// ParticipantSnapshot persists the participants observed in the initial
// mezon-sfu room_snapshot. Each username comes directly from the SFU member
// metadata; orchestrator persists it without doing another identity lookup.
func (c *Client) ParticipantSnapshot(ctx context.Context, roomName, roomID string, participants []Participant) error {
	if len(participants) == 0 {
		return nil
	}

	body, err := json.Marshal(map[string]any{
		"room_name":    roomName,
		"room_id":      roomID,
		"participants": participants,
	})
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode participant snapshot: %w", err)
	}

	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/api/v2/room-registry/participant/snapshot",
		strings.NewReader(string(body)),
	)
	if err != nil {
		return fmt.Errorf("orchestratorclient: build participant snapshot request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: participant snapshot: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf(
			"orchestratorclient: participant snapshot: HTTP %d",
			resp.StatusCode,
		)
	}

	return nil
}

// ParticipantJoined reports a Mezon-SFU peer_joined event for this exact
// room session, including the stable Mezon user ID and the display name from
// SFU member metadata.
func (c *Client) ParticipantJoined(ctx context.Context, roomName, roomID, participantIdentity string, username string) error {
	body, err := json.Marshal(map[string]string{
		"room_name":            roomName,
		"room_id":              roomID,
		"participant_identity": participantIdentity,
		"username":             username,
	})
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode participant_joined body: %w", err)
	}

	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/api/v2/room-registry/participant-joined",
		strings.NewReader(string(body)),
	)
	if err != nil {
		return fmt.Errorf("orchestratorclient: build participant_joined request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: participant_joined: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("orchestratorclient: participant_joined: HTTP %d", resp.StatusCode)
	}
	return nil
}

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
		return fmt.Errorf("orchestratorclient: encode push_chat_external body: %w", err)
	}
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/api/v2/agent_push_chat_external",
		strings.NewReader(string(body)),
	)
	if err != nil {
		return fmt.Errorf("orchestratorclient: build push_chat_external request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: push_chat_external http: %w", err)
	}

	defer func() {
		_ = resp.Body.Close()
	}()
	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("orchestratorclient: push_chat_external: HTTP %d", resp.StatusCode)
	}
	return nil
}

// UnregisterRoom releases the registration RegisterRoom created, and
// triggers orchestrator's room finalization (status -> terminal, summary
// generation) for roomID -- POST /api/v2/room-registry/unregister.
// roomID is the same value RegisterRoom was called with (or whatever
// registerRoomWithOrchestrator fell back to if registration never
// succeeded) -- passed through so orchestrator can compare-and-delete
// instead of deleting by roomName alone, in case roomName has already been
// re-registered by a new call by the time this lands (Mezon channels are a
// reused pool, see RegisterRoom's doc). A 404 (room_name not currently
// registered, e.g. RegisterRoom never succeeded) is not an error -- mirrors
// the old Python client's "consider not found as success".
func (c *Client) UnregisterRoom(ctx context.Context, roomName, roomID string) error {
	body, err := json.Marshal(map[string]string{"room_name": roomName, "room_id": roomID})
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode unregister_room body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v2/room-registry/unregister", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("orchestratorclient: build unregister_room request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: unregister_room: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 && resp.StatusCode != http.StatusNotFound {
		return fmt.Errorf("orchestratorclient: unregister_room: HTTP %d", resp.StatusCode)
	}
	return nil
}
