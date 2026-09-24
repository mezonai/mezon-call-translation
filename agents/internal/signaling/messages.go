// Package signaling implements the mezon-sfu WebSocket JSON protocol.
// Reference: mezon-sfu/CLAUDE.md section 4 (2026-08-16 protocol).
package signaling

import (
	"encoding/json"
	"fmt"
	"strings"
)

// envelope is used to sniff `type` before decoding the full payload.
type envelope struct {
	Type string `json:"type"`
}

// --- client -> server ---

type joinMsg struct {
	Type  string `json:"type"`
	Token string `json:"token"`
	Role  string `json:"role,omitempty"`
}

// answerMsg.OfferGeneration must echo back exactly the value the
// corresponding offerMsg carried (2026-08-22, mezon-sfu commit 2e01885) --
// not part of the SDP itself, a sibling JSON field. A mismatch or omission
// gets the answer rejected outright (missing_offer_generation /
// stale_offer_generation / future_offer_generation) before mezon-sfu even
// parses the SDP.
type answerMsg struct {
	Type            string `json:"type"`
	OfferGeneration int    `json:"offer_generation"`
	SDP             string `json:"sdp"`
}

// pongMsg is the only keepalive message the client sends -- mezon-sfu
// drives the ping/pong cycle itself (server pings every 10s, disconnects
// after 20s idle; see the "ping" case in Client.dispatch), so there's no
// pingMsg here for the client to proactively send.
type pongMsg struct {
	Type string `json:"type"`
}

// wireTypeSendMessage is the mezon-sfu WS wire type for posting a room chat
// message as this peer (mezon-sfu commit c41e59b "add send message"). The
// SFU echoes an ack (`message_sent`) to the sender and broadcasts
// `room_message` to every other peer in the room. Requires the peer to have
// finished joining (`joined_room` + `client_ufrag` set) -- otherwise the SFU
// replies with an `error` "must_join_room_first".
//
// The frame's `message` field is not the plain text -- it's a JSON-encoded
// RoomMessage ({id,name,avatar,timestamp,content}), which the SFU relays
// opaquely and the receiving side decodes back (see SendRoomMessage and the
// "room_message" case in dispatch -- the two must stay in sync).
//
// Deliberately NOT the same string as orchestratorclient's SSE
// agent-request type that triggers a send ("send_chat_message", see
// cmd/agent's registerRequestHandlers) -- one is this package's protocol
// with the SFU, the other is the orchestrator contract; keeping them
// separate constants means renaming one never silently moves the other.
const wireTypeSendMessage = "send_message"

// sendMessageMsg is the client -> SFU frame for wireTypeSendMessage.
// Message is a JSON-encoded RoomMessage, not raw text -- see the const doc.
type sendMessageMsg struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// encodeSendMessageFrame builds the send_message frame from a RoomMessage:
// trims Content, JSON-encodes the RoomMessage into the frame's `message`
// string, and rejects an empty or over-long result. Pure (no I/O) so the
// wire shape is testable on its own -- this is the format the receiving
// side already decodes back, mistakes here are silent until someone reads
// the chat.
func encodeSendMessageFrame(msg RoomMessage) (sendMessageMsg, error) {
	msg.Content = strings.TrimSpace(msg.Content)
	if msg.Content == "" {
		return sendMessageMsg{}, fmt.Errorf("signaling: room message is empty")
	}
	inner, err := json.Marshal(msg)
	if err != nil {
		return sendMessageMsg{}, fmt.Errorf("signaling: encode room message: %w", err)
	}
	// The SFU's limit (SFU_ROOM_MESSAGE_MAX_LEN) is on the `message` string
	// it extracts, i.e. this encoded blob -- not on Content alone.
	if len(inner) > maxRoomMessageBytes {
		return sendMessageMsg{}, fmt.Errorf("signaling: room message is %d bytes encoded, over the %d-byte limit", len(inner), maxRoomMessageBytes)
	}
	return sendMessageMsg{Type: wireTypeSendMessage, Message: string(inner)}, nil
}

// maxRoomMessageBytes bounds the encoded RoomMessage client-side so an
// over-long one fails fast here instead of after a WS round-trip. The SFU's
// own extraction buffer is 2048 bytes including the NUL terminator
// (SFU_ROOM_MESSAGE_MAX_LEN in signaling.c); 2000 leaves headroom for the
// {id,name,avatar,timestamp} envelope and is still far more than a chat
// line needs.
const maxRoomMessageBytes = 2000

// --- server -> client ---

// ICEServer mirrors mezon-sfu's iceServers entries (signaling.c:61-78):
// `urls` is a single string, not an array.
type ICEServer struct {
	URLs       string `json:"urls"`
	Username   string `json:"username,omitempty"`
	Credential string `json:"credential,omitempty"`
}

type joinedMsg struct {
	Type string `json:"type"`
	// Room: mezon-sfu sends this quoted (`"room":"1"`, signaling.c:59/75),
	// not a bare JSON number -- `,string` tells encoding/json to expect
	// that. Every int64/uint64 identifier field in this file has the same
	// treatment; only counts (participant_count) and peer_id/self_peer_id
	// are sent as bare numbers -- verified against signaling.c directly,
	// not assumed, since a wrong guess here fails to decode the *entire*
	// message, not just this field.
	Room       uint64      `json:"room,string"`
	ICEServers []ICEServer `json:"iceServers"`
}

type offerMsg struct {
	Type string `json:"type"`
	// OfferGeneration: 0 on the initial offer (right after joined), strictly
	// increasing on every renegotiate. Must be echoed back verbatim in the
	// answer -- see answerMsg.OfferGeneration.
	OfferGeneration int    `json:"offer_generation"`
	SDP             string `json:"sdp"`
}

// Member is the roster entry shape shared by room_snapshot.members[],
// peer_joined.peer and peer_updated.peer. See joinedMsg.Room's doc for why
// UserID needs `,string` while PeerID/MidAudio/MidVideo/MidScreen don't --
// verified per field against signaling.c (member-array construction around
// line 1643, peer_updated around line 1163), not assumed.
type Member struct {
	PeerID    uint64 `json:"peer_id"`
	UserID    int64  `json:"user_id,string"`
	Metadata  string `json:"metadata"` // "display_name;avatar_url"
	Role      string `json:"role"`
	IsMute    bool   `json:"is_mute"`
	Ufrag     string `json:"ufrag"`
	MidAudio  uint32 `json:"mid_audio"`
	MidVideo  uint32 `json:"mid_video"`
	MidScreen uint32 `json:"mid_screen"`
}

// ParseMemberMetadata splits the SFU metadata at the first semicolon. The
// display name is presentation data; use Member.UserID for participant identity.
// Metadata without a semicolon contains only a display name.
func ParseMemberMetadata(metadata string) (displayName, avatarURL string) {
	displayName, avatarURL, _ = strings.Cut(metadata, ";")
	return strings.TrimSpace(displayName), avatarURL
}

type roomSnapshotMsg struct {
	Type             string   `json:"type"`
	SelfPeerID       uint64   `json:"self_peer_id"`
	ParticipantCount int      `json:"participant_count"`
	Members          []Member `json:"members"`
}

type peerJoinedMsg struct {
	Type             string `json:"type"`
	ParticipantCount int    `json:"participant_count"`
	Peer             Member `json:"peer"`
}

type peerLeftMsg struct {
	Type             string `json:"type"`
	ParticipantCount int    `json:"participant_count"`
	Ufrag            string `json:"ufrag"`
	UserID           int64  `json:"user_id,string"`
	PeerID           uint64 `json:"peer_id"`
	MidAudio         uint32 `json:"mid_audio"`
	MidVideo         uint32 `json:"mid_video"`
	MidScreen        uint32 `json:"mid_screen"`
}

type peerUpdatedMsg struct {
	Type string `json:"type"`
	Peer Member `json:"peer"`
}

type errorMsg struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

type RoomMessage struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Avatar    string `json:"avatar"`
	Timestamp int64  `json:"timestamp"`
	Content   string `json:"content"`
}

type roomMessageMsg struct {
	Type    string `json:"type"`
	Message string `json:"message"`
	UserID  string `json:"user_id"`
	PeerID  uint64 `json:"peer_id"`
}

func decodeType(raw []byte) (string, error) {
	var e envelope
	if err := json.Unmarshal(raw, &e); err != nil {
		return "", err
	}
	return e.Type, nil
}
