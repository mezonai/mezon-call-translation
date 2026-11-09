// Package signaling implements the mezon-sfu WebSocket JSON protocol.
// Reference: mezon-sfu/CLAUDE.md section 4 (2026-08-16 protocol).
package signaling

import (
	"encoding/json"
	"strings"
)

// envelope is used to sniff `type` before decoding the full payload.
type envelope struct {
	Type string `json:"type"`
}

// --- client -> server ---

type joinMsg struct {
	Type             string `json:"type"`
	Token            string `json:"token"`
	Role             string `json:"role,omitempty"`
	MediaPoolVersion int    `json:"media_pool_version"`
}

func newJoinMsg(token, role string) joinMsg {
	return joinMsg{Type: "join", Token: token, Role: role, MediaPoolVersion: 1}
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

type muteMsg struct {
	Type   string `json:"type"`
	IsMute bool   `json:"is_mute"`
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
	Type string `json:"type"`
	// Room: mezon-sfu sends this quoted (`"room":"1"`, signaling.c:59/75),
	// not a bare JSON number -- `,string` tells encoding/json to expect
	// that. Every int64/uint64 identifier field in this file has the same
	// treatment; only counts (participant_count) and peer_id/self_peer_id
	// are sent as bare numbers -- verified against signaling.c directly,
	// not assumed, since a wrong guess here fails to decode the *entire*
	// message, not just this field.
	Room             uint64      `json:"room,string"`
	ICEServers       []ICEServer `json:"iceServers"`
	MediaPoolVersion int         `json:"media_pool_version"`
	MediaPoolMode    string      `json:"media_pool_mode"`
	MaxAudioSlots    uint32      `json:"max_audio_slots"`
	MaxCameraSlots   uint32      `json:"max_camera_slots"`
	MaxScreenSlots   uint32      `json:"max_screen_slots"`
}

const MediaPoolModeDecoupled = "decoupled"

// MediaPoolConfig is announced in `joined`. An absent/non-decoupled Mode is
// deliberately treated as legacy so one client binary works with old SFUs.
type MediaPoolConfig struct {
	Version        int
	Mode           string
	MaxAudioSlots  uint32
	MaxCameraSlots uint32
	MaxScreenSlots uint32
}

func (c MediaPoolConfig) IsDecoupled() bool { return c.Mode == MediaPoolModeDecoupled }

type MediaKind string

const (
	MediaKindAudio  MediaKind = "audio"
	MediaKindCamera MediaKind = "camera"
	MediaKindScreen MediaKind = "screen"
)

func (k MediaKind) Valid() bool {
	return k == MediaKindAudio || k == MediaKindCamera || k == MediaKindScreen
}

// MediaSlot is one decoupled receiving-pool binding carried in membership
// messages. Pointer fields in MediaSlots preserve JSON null/missing as nil.
type MediaSlot struct {
	Slot       uint32 `json:"slot"`
	Generation uint64 `json:"generation"`
	Mid        uint32 `json:"mid"`
}

type MediaSlots struct {
	Audio  *MediaSlot `json:"audio"`
	Camera *MediaSlot `json:"camera"`
	Screen *MediaSlot `json:"screen"`
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
//
// Slot/AssignmentGeneration are only ever meaningful on room_snapshot/
// peer_joined/peer_left (signaling.c's flush_membership_events, ~line 3570/
// 3596/3619) -- peer_updated's wire format (send_peer_updated/
// broadcast_peer_updated, ~line 2047/2086) never includes them at all, so
// they always decode as zero there. AssignmentGeneration==0 is mezon-sfu's
// own sentinel for "not a fixed-pool assignment" (membership_recipient_valid,
// signaling.c:3530: `if (recipient->assignment_generation == 0) return true`
// -- real assignments start counting from 1), so it's safe to gate on
// unconditionally rather than needing a separate "field present" signal --
// see rtcagent.PeerAgent.ApplyMemberSlot.
type Member struct {
	PeerID               uint64     `json:"peer_id"`
	UserID               int64      `json:"user_id,string"`
	Metadata             string     `json:"metadata"`
	Role                 string     `json:"role"`
	Ufrag                string     `json:"ufrag"`
	MidAudio             uint32     `json:"mid_audio"`
	MidVideo             uint32     `json:"mid_video"`
	MidScreen            uint32     `json:"mid_screen"`
	Slot                 uint32     `json:"slot"`
	AssignmentGeneration uint64     `json:"assignment_generation"`
	IsMute               bool       `json:"is_mute"`
	CameraRequested      bool       `json:"camera_requested"`
	CameraActive         bool       `json:"camera_active"`
	ScreenRequested      bool       `json:"screen_requested"`
	ScreenActive         bool       `json:"screen_active"`
	MediaSlots           MediaSlots `json:"media_slots"`
}

// ParseMemberMetadata splits the SFU metadata at the first semicolon using
// the "username;avatar" format. Username is presentation data; use
// Member.UserID for participant identity.
func ParseMemberMetadata(metadata string) (displayName, avatarURL string) {
	username, avatar, _ := strings.Cut(metadata, ";")
	return strings.TrimSpace(username), avatar
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
	Type             string     `json:"type"`
	ParticipantCount int        `json:"participant_count"`
	Ufrag            string     `json:"ufrag"`
	UserID           int64      `json:"user_id,string"`
	PeerID           uint64     `json:"peer_id"`
	MidAudio         uint32     `json:"mid_audio"`
	MidVideo         uint32     `json:"mid_video"`
	MidScreen        uint32     `json:"mid_screen"`
	MediaSlots       MediaSlots `json:"media_slots"`
}

type peerUpdatedMsg struct {
	Type string `json:"type"`
	Peer Member `json:"peer"`
}

// slotAssignedMsg/slotReleasedMsg: mezon-sfu PR #475 ("fixed pool" /
// max_active_speakers server config). A session's remote speaker slots are
// then pre-allocated once (mid_audio/video/screen fixed per slot, same SSRC
// for the slot's whole lifetime) instead of growing per peer -- reassigning
// a slot to a different peer does NOT renegotiate the SDP, so this is the
// *only* signal that a mid's content now belongs to someone else. See
// rtcagent.PeerAgent.ApplySlotAssigned's doc for why pion's OnTrack can't be
// relied on here.
type slotAssignedMsg struct {
	Type         string    `json:"type"`
	Slot         uint32    `json:"slot"`
	Generation   uint64    `json:"generation"`
	PeerID       uint64    `json:"peer_id"`
	UserID       int64     `json:"user_id,string"`
	MidAudio     uint32    `json:"mid_audio"`
	MidVideo     uint32    `json:"mid_video"`
	MidScreen    uint32    `json:"mid_screen"`
	AudioActive  bool      `json:"audio_active"`
	VideoActive  bool      `json:"video_active"`
	ScreenActive bool      `json:"screen_active"`
	Kind         MediaKind `json:"kind"`
	Mid          uint32    `json:"mid"`
	Active       bool      `json:"active"`
}

// slotReleasedMsg carries no peer identity -- just which slot/generation is
// now empty. The recipient must already know (from an earlier slot_assigned)
// which peer/mids that generation belonged to.
type slotReleasedMsg struct {
	Type       string    `json:"type"`
	Slot       uint32    `json:"slot"`
	Generation uint64    `json:"generation"`
	Kind       MediaKind `json:"kind"`
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
