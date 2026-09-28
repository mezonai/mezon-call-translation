package signaling

import (
	"errors"

	"github.com/gorilla/websocket"
)

// mezon-sfu WebSocket close codes this agent treats specially
// (sfu_disconnect_reason_t, agents/../mezon-sfu source of truth).
const (
	CloseKicked       = 4006 // kicked by admin
	CloseAloneTimeout = 4011 // alone participant timeout
	CloseDuplicate    = 4012 // a new session joined with the same user_id
)

// terminalCloseCodes are the only server-sent close codes after which the
// agent does not rejoin. Mirrors mezon-sfu's own frontend client
// (handleWebSocketClose), which the SFU team gave as the canonical
// reconnect policy on 2026-09-27, superseding an earlier, narrower
// allowlist-based version of this file: that switch reconnects by default
// and only special-cases 1000/4006/4011/4012 as "stop".
//
// The frontend's other two special cases -- 4003/4004/4005 (refresh the
// token, then reconnect) and 4013 (destroy the RTCPeerConnection, build a
// new one, then reconnect) -- need no equivalent here. runSession signs a
// brand new JWT and builds a brand new rtcagent.PeerAgent on every single
// call, never resuming a previous attempt's token or PeerConnection (see
// runSession's doc) -- so an ordinary reconnect already does both of those
// things unconditionally. They fall through to the same "reconnect with
// backoff" path as everything else below, with no special-casing needed.
var terminalCloseCodes = map[int]struct{}{
	websocket.CloseNormalClosure: {}, // 1000
	CloseKicked:                  {}, // 4006
	CloseAloneTimeout:            {}, // 4011
	CloseDuplicate:               {}, // 4012
}

// ShouldReconnect reports whether a session that ended with err should be
// retried. Defaults to true, matching the frontend switch's own `default:`
// case -- only one of the four terminal codes above can veto a retry. That
// includes every code not (yet) in sfu_disconnect_reason_t: an unknown
// close code is exactly the case the frontend's default arm exists for.
func ShouldReconnect(err error) bool {
	var ce *websocket.CloseError
	if !errors.As(err, &ce) {
		return true
	}
	_, terminal := terminalCloseCodes[ce.Code]
	return !terminal
}

// IsExpectedClose reports whether err is a terminal close that is a normal,
// graceful way for the agent's run to end (kicked, left alone too long,
// superseded by a new session, or a plain normal closure) -- as opposed to
// a crash or the reconnect budget running out. Same set as
// terminalCloseCodes: every terminal code here is a graceful end state,
// there is no separate "configuration error" bucket anymore.
func IsExpectedClose(err error) bool {
	var ce *websocket.CloseError
	if !errors.As(err, &ce) {
		return false
	}
	_, terminal := terminalCloseCodes[ce.Code]
	return terminal
}
