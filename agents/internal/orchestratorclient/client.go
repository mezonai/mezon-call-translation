// Package orchestratorclient talks to orchestrator_service: registers/
// unregisters this session's room, pushes transcripts, and listens for
// agent requests (tts_play, transcript_control) via SSE. Ported from the
// old Python agent's OrchestratorClient
// (agents/src/services/orchestrator_client.py) and AgentRequestHandler
// (agents/src/services/agent_request_handler.py) -- narrowed to what this
// pass actually wires up:
//   - register_room / unregister_room: kept, unchanged wire contract. Not
//     optional bookkeeping -- see RegisterRoom's doc for why skipping this
//     silently drops every recording event record-service reports for this
//     session (this was originally left unported, then found to be a real
//     gap and added back on 2026-08-18, see mezon-sfu-migration-plan.md).
//   - push_transcript: kept, unchanged wire contract.
//   - ReportTTSTranscript / ReportTTSCompleted (report_tts_transcript/
//     report_tts_completed): kept, added back 2026-08-18 alongside
//     register_room -- not optional either, see ReportTTSCompleted's doc.
//   - SSE agent-request listener: kept, unchanged wire contract
//     (GET /api/v2/sse/agent-requests?agent_id=&room_name=, text/event-stream).
//   - tts_play / transcript_control request handling: kept (drives
//     internal/ttsplayer and internal/audiopipeline.Bridge.SetSTTEnabled).
//
// NOT ported (out of scope for this pass, see mezon-sfu-migration-plan.md
// 2.5/2.6): push_event_session_started/ended -- checked against the old
// Python agent's own codebase, these were dead code there too (defined on
// OrchestratorClient, never called from main.go/lifecycle_manager.py), and
// orchestrator_service's register_room/unregister_room already push the
// equivalent room_started/room_ended events themselves as a side effect
// (room_registry_api.py) -- an explicit call here would just be a
// duplicate. Also not ported: send_chat_message (was LiveKit DataChannel,
// mezon-sfu has no data channel yet).
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
// (record-service's SessionMeta.RoomID and the TTS reporting above, see
// RegisterRoom's doc).
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

	handlers map[string]RequestHandler
}

// RequestHandler processes one agent request's payload. Errors are logged,
// not propagated -- one bad/unhandleable request must never take down the
// SSE listener.
type RequestHandler func(payload map[string]any) error

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL:  strings.TrimRight(baseURL, "/"),
		apiKey:   apiKey,
		http:     &http.Client{Timeout: 5 * time.Second},
		handlers: make(map[string]RequestHandler),
	}
}

// RegisterHandler wires a handler for one request_type (e.g. "tts_play",
// "transcript_control"). Call before RunAgentRequestListener.
func (c *Client) RegisterHandler(requestType string, h RequestHandler) {
	c.handlers[requestType] = h
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

// PushTranscript sends one transcript line. Best-effort/fire-and-forget,
// matching the old TranscriptManager: caller should log a failure, not
// treat it as fatal to the STT pipeline.
//
// roomName: see the package doc -- this is cfg.RoomID as a decimal string,
// not a confirmed match for whatever orchestrator's push_transcript
// actually expects under the new protocol.
func (c *Client) PushTranscript(ctx context.Context, roomName, text, participantIdentity, messageType string) error {
	body, err := json.Marshal(map[string]string{
		"room_name":            roomName,
		"message":              text,
		"message_type":         messageType,
		"participant_identity": participantIdentity,
	})
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode push_transcript body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v2/push_transcript", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("orchestratorclient: build push_transcript request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: push_transcript: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("orchestratorclient: push_transcript: HTTP %d", resp.StatusCode)
	}
	return nil
}

// ReportTTSTranscript reports one utterance segment for the agent's own TTS
// track directly to orchestrator (event "tts.transcript") -- POST
// /api/v2/recordings/events. Ported from the old Python agent's
// TTSManager._report_tts_transcript. No Whisper job ever transcribes this
// track (orchestrator skips STT for the agent's own track id, since the
// text is already known -- it's what was synthesized), so this is the only
// source of transcript text for it.
//
// roomID must be orchestrator's own room UUID (see RegisterRoom's doc), not
// mezon-sfu's numeric room id. start/end are seconds relative to this
// session's record-service forwarder start time, same convention
// recording.started/.completed use for every other track.
func (c *Client) ReportTTSTranscript(ctx context.Context, roomID, trackID, text string, start, end float64) error {
	return c.postRecordingEvent(ctx, map[string]any{
		"event":    "tts.transcript",
		"room_id":  roomID,
		"track_id": trackID,
		"text":     text,
		"start":    start,
		"end":      end,
	})
}

// ReportTTSCompleted marks the agent's own TTS track as done (event
// "tts.completed") -- POST /api/v2/recordings/events. This is not optional:
// orchestrator's room-finalization check treats this as the TTS track's
// only terminal-status signal (see recording_event_service.py's
// handle_tts_transcript_event and RecordingEventRequest.skip_stt for the
// agent's own track) -- skip it and, on any session where the agent ever
// spoke, that room's status stays stuck at "wait_process" forever and its
// summary never generates. Call exactly once, when done speaking for the
// session (mirrors ttsplayer.Player.Close, and only if at least one
// utterance was ever forwarded to record-service -- see the old Python
// TTSManager.cleanup's same guard).
func (c *Client) ReportTTSCompleted(ctx context.Context, roomID, trackID string) error {
	return c.postRecordingEvent(ctx, map[string]any{
		"event":    "tts.completed",
		"room_id":  roomID,
		"track_id": trackID,
	})
}

func (c *Client) postRecordingEvent(ctx context.Context, payload map[string]any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("orchestratorclient: encode recordings/events body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v2/recordings/events", strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("orchestratorclient: build recordings/events request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	c.authHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("orchestratorclient: recordings/events: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("orchestratorclient: recordings/events: HTTP %d", resp.StatusCode)
	}
	return nil
}
