package orchestratorclient

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSummaryMetadataFlow(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v2/mezon/bot/login", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Account struct {
				AppID string `json:"appid"`
				Token string `json:"token"`
			} `json:"account"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body.Account.AppID != "bot-id" || body.Account.Token != "bot-token" {
			t.Fatalf("unexpected credentials: %+v", body.Account)
		}
		_ = json.NewEncoder(w).Encode(BotSession{AccessToken: "orch-token", ExpiresIn: 3600})
	})
	mux.HandleFunc("GET /api/v2/sse/metadata", func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer orch-token" {
			t.Fatalf("authorization = %q", got)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = fmt.Fprint(w, "event: connected\ndata: {\"connection_id\":\"1\"}\n\n")
		_, _ = fmt.Fprint(w, "event: heartbeat\ndata: ping\n\n")
		_, _ = fmt.Fprint(w, "data: {\"event_id\":\"event-1\",\"event_type\":\"room_summary_done\",\"room\":{\"room_id\":\"room-1\",\"room_name\":\"daily\"}}\n\n")
	})
	mux.HandleFunc("GET /api/v2/summary/room/id/room-1", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok",
			"data": map[string]any{
				"room_id":   "room-1",
				"room_name": "daily",
				"summary_data": map[string]any{
					"summary":      "Deployment approved.",
					"action_items": map[string][]string{"123": {"Deploy Friday"}},
				},
			},
		})
	})
	mux.HandleFunc("GET /api/v2/rooms/participant/room-1", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok",
			"participants": []map[string]any{
				{"identity": "123", "name": "Tina", "state": "ACTIVE", "joined_at": 1},
			},
		})
	})

	server := httptest.NewServer(mux)
	defer server.Close()
	client := New(server.URL, "")

	session, err := client.AuthenticateBot(context.Background(), "bot-id", "bot-token")
	if err != nil {
		t.Fatal(err)
	}

	var received MetadataEvent
	err = client.StreamMetadata(context.Background(), session.AccessToken, func(event MetadataEvent) error {
		received = event
		return nil
	})
	if err == nil || received.EventID != "event-1" || received.Room.RoomID != "room-1" {
		t.Fatalf("stream err=%v event=%+v", err, received)
	}

	summary, err := client.GetRoomSummary(context.Background(), session.AccessToken, received.Room.RoomID)
	if err != nil {
		t.Fatal(err)
	}
	if summary.SummaryData.Summary != "Deployment approved." {
		t.Fatalf("summary = %+v", summary)
	}

	participants, err := client.GetRoomParticipants(context.Background(), session.AccessToken, received.Room.RoomID)
	if err != nil {
		t.Fatal(err)
	}
	if len(participants) != 1 || participants[0].Identity != "123" {
		t.Fatalf("participants = %+v", participants)
	}
}
