package signaling

import (
	"errors"
	"fmt"
	"testing"

	"github.com/gorilla/websocket"
)

func closeErr(code int) error {
	return fmt.Errorf("signaling: read: %w", &websocket.CloseError{Code: code})
}

func TestShouldReconnect(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{"normal closure", closeErr(websocket.CloseNormalClosure), false},
		{"kicked", closeErr(4006), false},
		{"alone timeout", closeErr(4011), false},
		{"duplicate session", closeErr(4012), false},

		// Everything else reconnects, per the frontend's `default:` arm --
		// including codes with their own special client-side handling
		// there (token refresh, PeerConnection reset) that an agent
		// reconnect already does unconditionally, and codes not (yet) in
		// sfu_disconnect_reason_t at all.
		{"going away", closeErr(websocket.CloseGoingAway), true},                              // 1001
		{"abnormal closure (no close frame)", closeErr(websocket.CloseAbnormalClosure), true}, // 1006
		{"internal error", closeErr(1011), true},
		{"idle timeout", closeErr(4001), true},
		{"ping failed", closeErr(4002), true},
		{"auth not configured", closeErr(4003), true},
		{"missing token", closeErr(4004), true},
		{"invalid token", closeErr(4005), true},
		{"recv error", closeErr(4008), true},
		{"transport error", closeErr(4010), true},
		{"dtls/transport failure", closeErr(4013), true},
		{"unknown future code", closeErr(4999), true},
		{"no status", closeErr(websocket.CloseNoStatusReceived), true},
		{"dial failure (no close frame at all)", errors.New("dial tcp: connection refused"), true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ShouldReconnect(tt.err); got != tt.want {
				t.Errorf("ShouldReconnect(%v) = %v, want %v", tt.err, got, tt.want)
			}
		})
	}
}

func TestIsExpectedClose(t *testing.T) {
	for _, code := range []int{1000, 4006, 4011, 4012} {
		if !IsExpectedClose(closeErr(code)) {
			t.Errorf("code %d should be an expected close", code)
		}
	}
	for _, code := range []int{1001, 1006, 1011, 4001, 4002, 4003, 4004, 4005, 4008, 4010, 4013} {
		if IsExpectedClose(closeErr(code)) {
			t.Errorf("code %d should not be an expected close (it reconnects instead)", code)
		}
	}
	if IsExpectedClose(errors.New("boom")) {
		t.Error("non-close error should not be an expected close")
	}
}

func TestServerErrorReconnectPolicy(t *testing.T) {
	tests := []struct {
		message   string
		permanent bool
	}{
		{"unsupported_media_pool_version", true},
		// Action-level errors (sfu guide section 11) must not end the session.
		{"camera_capacity_full", false},
		{"screen_share_capacity_full", false},
		{"invalid_camera_active", false},
		{"", false},
	}
	for _, tt := range tests {
		t.Run(tt.message, func(t *testing.T) {
			err := fmt.Errorf("signaling: dispatch: %w", &ServerError{Message: tt.message})
			var serverErr *ServerError
			if !errors.As(err, &serverErr) || serverErr.Permanent() != tt.permanent {
				t.Fatalf("Permanent(%q) = %v, want %v", tt.message, serverErr != nil && serverErr.Permanent(), tt.permanent)
			}
			if got := ShouldReconnect(err); got == tt.permanent {
				t.Errorf("ShouldReconnect(%q) = %v, want %v", tt.message, got, !tt.permanent)
			}
			if IsExpectedClose(err) {
				t.Errorf("server error %q must not count as a graceful close", tt.message)
			}
		})
	}
}

func TestDispatchErrorMessageReturnsServerError(t *testing.T) {
	c := &Client{}
	err := c.dispatch("error", []byte(`{"type":"error","message":"unsupported_media_pool_version"}`))
	var serverErr *ServerError
	if !errors.As(err, &serverErr) {
		t.Fatalf("dispatch(error) = %v, want *ServerError", err)
	}
	if serverErr.Message != "unsupported_media_pool_version" || !serverErr.Permanent() {
		t.Fatalf("server error = %+v", serverErr)
	}
}
