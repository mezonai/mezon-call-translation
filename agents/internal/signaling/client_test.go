package signaling

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gorilla/websocket"
)

func TestSetMuteWritesUnmuteFrame(t *testing.T) {
	frames := make(chan map[string]any, 2)
	serverErr := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := (&websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}).Upgrade(w, r, nil)
		if err != nil {
			serverErr <- err
			return
		}
		defer conn.Close()

		for range 2 {
			_, raw, err := conn.ReadMessage()
			if err != nil {
				serverErr <- err
				return
			}
			var frame map[string]any
			if err := json.Unmarshal(raw, &frame); err != nil {
				serverErr <- err
				return
			}
			frames <- frame
		}
	}))
	defer server.Close()

	client, err := Dial(context.Background(), "ws"+strings.TrimPrefix(server.URL, "http"), "token", "speaker", Callbacks{})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer client.Close()
	if err := client.SetMute(false); err != nil {
		t.Fatalf("SetMute(false): %v", err)
	}

	join := <-frames
	if join["type"] != "join" || join["media_pool_version"] != float64(1) {
		t.Fatalf("first frame = %v, want versioned join", join)
	}
	unmute := <-frames
	if unmute["type"] != "mute" || unmute["is_mute"] != false {
		t.Fatalf("second frame = %v, want mute false", unmute)
	}

	select {
	case err := <-serverErr:
		t.Fatalf("server: %v", err)
	default:
	}
}
