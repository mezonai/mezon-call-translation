package signaling

import (
	"errors"

	"github.com/gorilla/websocket"
)

// mezon-sfu application-specific WebSocket close codes (sfu_disconnect_reason_t).
const (
	CloseIdleTimeout    = 4001 // client idle too long
	ClosePingFailed     = 4002 // server failed to send ping
	CloseKicked         = 4006 // kicked by admin
	CloseRecvError      = 4008 // WebSocket recv failure
	CloseTransportError = 4010 // UV_DISCONNECT / poll error
	CloseAloneTimeout   = 4011 // alone participant timeout
	CloseDuplicate      = 4012 // new session joined with same user_id
)

// reconnectableCloseCodes is the only set of server-sent close codes after
// which the agent rejoins (per the SFU team). Every other explicit close
// code ends the run.
var reconnectableCloseCodes = map[int]struct{}{
	CloseIdleTimeout:    {},
	ClosePingFailed:     {},
	CloseRecvError:      {},
	CloseTransportError: {},
}

// ShouldReconnect reports whether a session that ended with err should be
// retried. Only an explicit close code sent by the SFU can veto a retry;
// anything without one (dial failure, connection reset, and gorilla's
// synthetic 1005/1006 for a drop with no close frame) stays retryable.
func ShouldReconnect(err error) bool {
	var ce *websocket.CloseError
	if !errors.As(err, &ce) {
		return true
	}
	switch ce.Code {
	case websocket.CloseNoStatusReceived, websocket.CloseAbnormalClosure:
		return true
	}
	_, ok := reconnectableCloseCodes[ce.Code]
	return ok
}

// IsExpectedClose reports whether err is an SFU close that is a normal way
// for the agent's run to end (as opposed to a configuration/auth failure).
func IsExpectedClose(err error) bool {
	var ce *websocket.CloseError
	if !errors.As(err, &ce) {
		return false
	}
	switch ce.Code {
	case websocket.CloseNormalClosure, CloseKicked, CloseAloneTimeout, CloseDuplicate:
		return true
	}
	return false
}
