package signaling

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func fakeSFU(t *testing.T, handle func(*websocket.Conn)) string {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
		_ = conn.SetWriteDeadline(time.Now().Add(3 * time.Second))
		handle(conn)
	}))
	t.Cleanup(server.Close)
	return "ws" + strings.TrimPrefix(server.URL, "http")
}

func TestDialAdvertisesMediaPoolVersionForEveryRole(t *testing.T) {
	for _, role := range []string{"audience", "speaker"} {
		t.Run(role, func(t *testing.T) {
			received := make(chan joinMsg, 1)
			url := fakeSFU(t, func(conn *websocket.Conn) {
				var msg joinMsg
				if err := conn.ReadJSON(&msg); err != nil {
					t.Errorf("read join: %v", err)
				}
				received <- msg
			})
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			client, err := Dial(ctx, url, "test-token", role, Callbacks{})
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			select {
			case msg := <-received:
				if msg.Type != "join" || msg.Token != "test-token" || msg.Role != role || msg.MediaPoolVersion != 1 {
					t.Fatalf("actual Dial join payload = %+v", msg)
				}
			case <-ctx.Done():
				t.Fatal("Dial did not send join")
			}
		})
	}
}

func TestRunKeepsConnectionForMediaRefusals(t *testing.T) {
	for _, reason := range []string{
		"camera_capacity_full", "camera_update_failed", "screen_share_capacity_full", "screen_share_update_failed",
		"camera_not_allowed", "screen_share_not_allowed", "invalid_camera_active", "invalid_screen_share_active",
	} {
		t.Run(reason, func(t *testing.T) {
			result := make(chan error, 1)
			url := fakeSFU(t, func(conn *websocket.Conn) {
				var join joinMsg
				if err := conn.ReadJSON(&join); err != nil {
					result <- err
					return
				}
				if err := conn.WriteJSON(errorMsg{Type: "error", Message: reason}); err != nil {
					result <- err
					return
				}
				if err := conn.WriteJSON(map[string]string{"type": "ping"}); err != nil {
					result <- err
					return
				}
				var pong pongMsg
				if err := conn.ReadJSON(&pong); err != nil {
					result <- err
					return
				}
				if pong.Type != "pong" {
					result <- fmt.Errorf("got %q, want pong after refusal", pong.Type)
					return
				}
				result <- nil
			})
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			client, err := Dial(ctx, url, "token", "audience", Callbacks{})
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			// The fake server closes only after receiving pong (or its deadline).
			_ = client.Run(ctx)
			select {
			case err := <-result:
				if err != nil {
					t.Fatal(err)
				}
			case <-ctx.Done():
				t.Fatal("Run did not continue after media refusal")
			}
		})
	}
}

func TestRunPreservesPermanentVersionError(t *testing.T) {
	url := fakeSFU(t, func(conn *websocket.Conn) {
		var join joinMsg
		if err := conn.ReadJSON(&join); err != nil {
			t.Errorf("read join: %v", err)
			return
		}
		if err := conn.WriteJSON(errorMsg{Type: "error", Message: "unsupported_media_pool_version"}); err != nil {
			t.Errorf("write refusal: %v", err)
			return
		}
		// Do not close first: the protocol error itself must terminate Run.
		_, _, _ = conn.ReadMessage()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	client, err := Dial(ctx, url, "token", "audience", Callbacks{})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	err = client.Run(ctx)
	var protocolErr *ServerError
	if !errors.As(err, &protocolErr) || !protocolErr.Permanent() {
		t.Fatalf("Run returned %v, want permanent protocol error before socket close", err)
	}
	wrapped := fmt.Errorf("session failed: %w", err)
	if ShouldReconnect(wrapped) || IsExpectedClose(wrapped) {
		t.Fatal("unsupported version must stop retries and remain an unexpected/configuration failure")
	}
}
