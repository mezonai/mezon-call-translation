// Package orchestratorclient talks to orchestrator_service: pushes
// transcripts, and listens for agent requests (tts_play, transcript_control)
// via SSE. Ported from the old Python agent's OrchestratorClient
// (agents/src/services/orchestrator_client.py) and AgentRequestHandler
// (agents/src/services/agent_request_handler.py) -- narrowed to what this
// pass actually wires up:
//   - push_transcript: kept, unchanged wire contract.
//   - SSE agent-request listener: kept, unchanged wire contract
//     (GET /api/v2/sse/agent-requests?agent_id=&room_name=, text/event-stream).
//   - tts_play / transcript_control request handling: kept (drives
//     internal/ttsplayer and internal/audiopipeline.Bridge.SetSTTEnabled).
//
// NOT ported (out of scope for this pass, see mezon-sfu-migration-plan.md
// 2.5/2.6): register_room/unregister_room, push_event_session_started/ended,
// report_tts_transcript/report_tts_completed, send_chat_message (was
// LiveKit DataChannel, mezon-sfu has no data channel yet). All of those are
// tied to the old room_name (LiveKit room name / meeting code) vs room_id
// (orchestrator's internal UUID) split, which doesn't have a settled
// equivalent under mezon-sfu (mezon-sfu only has a numeric room_id) -- this
// client uses room_id (as a decimal string) wherever the old contract
// wanted room_name, which is an assumption, not a confirmed contract. See
// the room_name parameter docs below.
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
