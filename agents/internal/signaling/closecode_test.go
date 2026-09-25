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
		{"idle timeout", closeErr(4001), true},
		{"ping failed", closeErr(4002), true},
		{"recv error", closeErr(4008), true},
		{"transport error", closeErr(4010), true},
		{"poll start failed", closeErr(4009), false},
		{"kicked", closeErr(4006), false},
		{"alone timeout", closeErr(4011), false},
		{"duplicate session", closeErr(4012), false},
		{"invalid token", closeErr(4005), false},
		{"normal closure", closeErr(websocket.CloseNormalClosure), false},
		{"going away", closeErr(websocket.CloseGoingAway), false},
		{"abnormal closure (no close frame)", closeErr(websocket.CloseAbnormalClosure), true},
		{"no status", closeErr(websocket.CloseNoStatusReceived), true},
		{"dial failure", errors.New("dial tcp: connection refused"), true},
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
	for _, code := range []int{4003, 4004, 4005, 4007} {
		if IsExpectedClose(closeErr(code)) {
			t.Errorf("code %d should not be an expected close", code)
		}
	}
	if IsExpectedClose(errors.New("boom")) {
		t.Error("non-close error should not be an expected close")
	}
}
