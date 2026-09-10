package signaling

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// PeerLeftEvent mirrors peerLeftMsg minus the `type` field.
type PeerLeftEvent struct {
	ParticipantCount int
	Ufrag            string
	UserID           int64
	PeerID           uint64
	MidAudio         uint32
	MidVideo         uint32
	MidScreen        uint32
}

// Callbacks lets the caller (rtcagent) react to server messages without this
// package depending on pion. Every callback is invoked from the single read
// loop goroutine (see Client.Run) -- never concurrently.
type Callbacks struct {
	// OnJoined fires once, right after `joined`, before any offer.
	OnJoined func(room uint64, iceServers []ICEServer)
	// OnOffer must set the remote description, build+set the local answer,
	// and return the answer SDP to send back. Called for the initial offer
	// and every renegotiate.
	OnOffer        func(sdp string) (answerSDP string, err error)
	OnRoomSnapshot func(selfPeerID uint64, participantCount int, members []Member)
	OnPeerJoined   func(participantCount int, peer Member)
	OnPeerLeft     func(ev PeerLeftEvent)
	OnPeerUpdated  func(peer Member)
	OnRoomMessage  func(message RoomMessage, userID string)
}

// Client is a single-use WS signaling session. mezon-sfu treats a WS
// disconnect as the session being dead (mezon-sfu-migration-plan.md 2.2) --
// this client does not auto-reconnect. Decision: reconnect/respawn is the
// worker manager's job (it owns the subprocess lifecycle and already has to
// detect process exit for the stop path), not this client's. On any
// unrecoverable error Run returns, the caller should exit the process and
// let the worker manager respawn if appropriate.
type Client struct {
	conn *websocket.Conn
	cb   Callbacks

	// writeMu serializes all writes to conn. Until send_message, every write
	// happened on the single Run read-loop goroutine (pong/answer), so no
	// lock was needed. SendRoomMessage is called from a different goroutine
	// (cmd/agent's SSE agent-request handler), and gorilla/websocket allows
	// only one concurrent writer -- without this the two can interleave and
	// corrupt a frame.
	writeMu sync.Mutex
}

// Dial connects and completes the join handshake up through the first
// `room_snapshot` (i.e. steps 3-6 of mezon-sfu-migration-checklist.md C.2).
// cb must be fully populated before calling Dial since OnOffer/OnJoined can
// fire before Dial returns.
func Dial(ctx context.Context, wsURL, token string, role string, cb Callbacks) (*Client, error) {
	// 5s: guards against mezon-sfu/network being slow, not genuinely
	// necessary work -- a local WS handshake normally completes in well
	// under 1s, so this only ever matters when something's actually wrong,
	// in which case failing fast (and letting the worker manager's own
	// retry/backoff take over) beats a long silent wait.
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, resp, err := dialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return nil, fmt.Errorf("signaling: dial %s: %w", wsURL, err)
	}
	if resp != nil {
		_ = resp.Body.Close()
	}

	c := &Client{conn: conn, cb: cb}

	join := joinMsg{Type: "join", Token: token, Role: role}
	if err := c.send(join); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("signaling: send join: %w", err)
	}

	return c, nil
}

// Run blocks reading and dispatching server messages until the connection
// closes or ctx is cancelled. It returns the terminal error, if any.
func (c *Client) Run(ctx context.Context) error {
	go func() {
		<-ctx.Done()
		_ = c.conn.Close()
	}()

	for {
		_, raw, err := c.conn.ReadMessage()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("signaling: read: %w", err)
		}

		msgType, err := decodeType(raw)
		if err != nil {
			logging.L.Warn("signaling: undecodable message", logging.ErrAttrs(err)...)
			continue
		}

		if err := c.dispatch(msgType, raw); err != nil {
			logging.L.Error("signaling: handler error", append(logging.ErrAttrs(err), "type", msgType)...)
		}
	}
}

func (c *Client) dispatch(msgType string, raw []byte) error {
	switch msgType {
	case "joined":
		var m joinedMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode joined: %w", err)
		}
		if c.cb.OnJoined != nil {
			c.cb.OnJoined(m.Room, m.ICEServers)
		}

	case "offer":
		var m offerMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode offer: %w", err)
		}
		if c.cb.OnOffer == nil {
			return fmt.Errorf("received offer but no OnOffer handler registered")
		}
		answer, err := c.cb.OnOffer(m.SDP)
		if err != nil {
			return fmt.Errorf("handle offer: %w", err)
		}
		if err := c.send(answerMsg{Type: "answer", OfferGeneration: m.OfferGeneration, SDP: answer}); err != nil {
			return fmt.Errorf("send answer: %w", err)
		}

	case "room_snapshot":
		var m roomSnapshotMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode room_snapshot: %w", err)
		}
		if c.cb.OnRoomSnapshot != nil {
			c.cb.OnRoomSnapshot(m.SelfPeerID, m.ParticipantCount, m.Members)
		}

	case "peer_joined":
		var m peerJoinedMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode peer_joined: %w", err)
		}
		if c.cb.OnPeerJoined != nil {
			c.cb.OnPeerJoined(m.ParticipantCount, m.Peer)
		}

	case "peer_left":
		var m peerLeftMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode peer_left: %w", err)
		}
		if c.cb.OnPeerLeft != nil {
			c.cb.OnPeerLeft(PeerLeftEvent{
				ParticipantCount: m.ParticipantCount,
				Ufrag:            m.Ufrag,
				UserID:           m.UserID,
				PeerID:           m.PeerID,
				MidAudio:         m.MidAudio,
				MidVideo:         m.MidVideo,
				MidScreen:        m.MidScreen,
			})
		}

	case "peer_updated":
		var m peerUpdatedMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode peer_updated: %w", err)
		}
		if c.cb.OnPeerUpdated != nil {
			c.cb.OnPeerUpdated(m.Peer)
		}

	case "ping":
		// Server-initiated keepalive (every 10s, 20s idle timeout -- see
		// mezon-sfu/src/protocol/signaling/signaling.h). Must reply promptly.
		if err := c.send(pongMsg{Type: "pong"}); err != nil {
			return fmt.Errorf("send pong: %w", err)
		}

	case "pong":
		// Reply to a ping we sent; nothing to do.

	case "error":
		var m errorMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode error message: %w", err)
		}
		return fmt.Errorf("server error: %s", m.Message)

	case "role_changed", "visibility_changed", "mute_changed", "screen_share_changed":
		// Acks for our own state-changing messages; not used by the
		// record-only (audience) path yet. Logged for observability.
		logging.L.Info("signaling: ack", "type", msgType)

	case "message_sent":
		// Ack for our own SendRoomMessage; no request-id correlation, so
		// nothing actionable -- logged for observability only.
		logging.L.Info("signaling: ack", "type", msgType)

	case "room_message":
		var m roomMessageMsg
		if err := json.Unmarshal(raw, &m); err != nil {
			return fmt.Errorf("decode room_message: %w", err)
		}
		var message RoomMessage
		if err := json.Unmarshal([]byte(m.Message), &message); err != nil {
			return fmt.Errorf("decode room_message payload: %w", err)
		}
		if c.cb.OnRoomMessage != nil {
			c.cb.OnRoomMessage(message, m.UserID)
		}

	default:
		logging.L.Warn("signaling: unknown message type", "type", msgType)
	}

	return nil
}

func (c *Client) send(v any) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return c.conn.WriteJSON(v)
}

// SendRoomMessage posts text into the room's chat as this peer (mezon-sfu
// wireTypeSendMessage). Best-effort: the SFU broadcasts it to other peers
// and acks with `message_sent`, but there's no per-message result on the
// wire, so a success here only means the frame was written -- a later
// `error` "must_join_room_first" (peer not fully joined yet) or
// "invalid_message" only shows up as a logged handler error in Run's loop.
//
// Safe to call from any goroutine (see writeMu). Rejects an empty or
// over-long message locally rather than after a WS round-trip.
func (c *Client) SendRoomMessage(text string) error {
	text = strings.TrimSpace(text)
	if text == "" {
		return fmt.Errorf("signaling: room message is empty")
	}
	if len(text) > maxRoomMessageBytes {
		return fmt.Errorf("signaling: room message is %d bytes, over the %d-byte limit", len(text), maxRoomMessageBytes)
	}
	if err := c.send(sendMessageMsg{Type: wireTypeSendMessage, Message: text}); err != nil {
		return fmt.Errorf("signaling: send room message: %w", err)
	}
	return nil
}

// Close closes the underlying connection. mezon-sfu treats a closed WS as
// the peer leaving -- no explicit `leave` message is needed (mezon-sfu/CLAUDE.md 4.2).
func (c *Client) Close() error {
	return c.conn.Close()
}
