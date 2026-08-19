// Package signaling implements the mezon-sfu WebSocket JSON protocol.
// Reference: mezon-sfu/CLAUDE.md section 4 (2026-08-16 protocol).
package signaling

import "encoding/json"

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

type answerMsg struct {
	Type string `json:"type"`
	SDP  string `json:"sdp"`
}

// pongMsg is the only keepalive message the client sends -- mezon-sfu
// drives the ping/pong cycle itself (server pings every 10s, disconnects
// after 20s idle; see the "ping" case in Client.dispatch), so there's no
// pingMsg here for the client to proactively send.
type pongMsg struct {
	Type string `json:"type"`
}

// --- server -> client ---

// ICEServer mirrors mezon-sfu's iceServers entries (signaling.c:61-78):
// `urls` is a single string, not an array.
type ICEServer struct {
	URLs       string `json:"urls"`
	Username   string `json:"username,omitempty"`
	Credential string `json:"credential,omitempty"`
}

type joinedMsg struct {
	Type       string      `json:"type"`
	Room       uint64      `json:"room"`
	ICEServers []ICEServer `json:"iceServers"`
}

type offerMsg struct {
	Type string `json:"type"`
	SDP  string `json:"sdp"`
}

// Member is the roster entry shape shared by room_snapshot.members[],
// peer_joined.peer and peer_updated.peer.
type Member struct {
	PeerID    uint64 `json:"peer_id"`
	UserID    int64  `json:"user_id"`
	Role      string `json:"role"`
	IsMute    bool   `json:"is_mute"`
	Ufrag     string `json:"ufrag"`
	MidAudio  string `json:"mid_audio"`
	MidVideo  string `json:"mid_video"`
	MidScreen string `json:"mid_screen"`
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
	UserID           int64  `json:"user_id"`
	PeerID           uint64 `json:"peer_id"`
	MidAudio         string `json:"mid_audio"`
	MidVideo         string `json:"mid_video"`
	MidScreen        string `json:"mid_screen"`
}

type peerUpdatedMsg struct {
	Type string `json:"type"`
	Peer Member `json:"peer"`
}

type errorMsg struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

func decodeType(raw []byte) (string, error) {
	var e envelope
	if err := json.Unmarshal(raw, &e); err != nil {
		return "", err
	}
	return e.Type, nil
}
