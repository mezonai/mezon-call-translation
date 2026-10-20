package signaling

import (
	"encoding/json"
	"testing"
)

func TestJoinAlwaysAdvertisesMediaPoolVersion(t *testing.T) {
	raw, err := json.Marshal(newJoinMsg("token", "audience"))
	if err != nil {
		t.Fatalf("marshal join: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("decode join: %v", err)
	}
	if got["media_pool_version"] != float64(1) {
		t.Fatalf("media_pool_version = %v, want 1", got["media_pool_version"])
	}
}

// These payloads are copied verbatim from mezon-sfu's actual snprintf format
// strings (src/protocol/signaling/signaling.c), not hand-guessed -- this is
// what caught the 2026-08-19 bug where `room`/`user_id` are sent quoted
// ("room":"1") while `peer_id`/`mid_audio`/`mid_video`/`mid_screen` are
// sent as bare numbers, and every struct in messages.go had at least one of
// those backwards.

func TestDecodeJoined(t *testing.T) {
	raw := []byte(`{"type":"joined","room":"1","iceServers":[{"urls":"stun:127.0.0.1:3478"}]}`)
	var m joinedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode joined: %v", err)
	}
	if m.Room != 1 {
		t.Errorf("Room = %d, want 1", m.Room)
	}
	if len(m.ICEServers) != 1 || m.ICEServers[0].URLs != "stun:127.0.0.1:3478" {
		t.Errorf("ICEServers = %+v", m.ICEServers)
	}
}

func TestDecodeJoinedDecoupled(t *testing.T) {
	raw := []byte(`{"type":"joined","room":"1","iceServers":[],"media_pool_version":1,` +
		`"media_pool_mode":"decoupled","max_audio_slots":6,"max_camera_slots":4,"max_screen_slots":2}`)
	var m joinedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode joined: %v", err)
	}
	if m.MediaPoolVersion != 1 || m.MediaPoolMode != MediaPoolModeDecoupled ||
		m.MaxAudioSlots != 6 || m.MaxCameraSlots != 4 || m.MaxScreenSlots != 2 {
		t.Fatalf("decoupled joined fields = %+v", m)
	}
}

func TestDecodeOffer(t *testing.T) {
	raw := []byte(`{"type":"offer","offer_generation":3,"sdp":"v=0..."}`)
	var m offerMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode offer: %v", err)
	}
	if m.OfferGeneration != 3 || m.SDP != "v=0..." {
		t.Errorf("m = %+v", m)
	}
}

// TestAnswerEchoesOfferGeneration guards the 2026-08-22 breaking change
// (mezon-sfu commit 2e01885): offer_generation is a JSON field sibling to
// sdp, not part of the SDP body, and the answer must echo the offer's value
// back verbatim or mezon-sfu rejects it before even parsing the SDP.
func TestAnswerEchoesOfferGeneration(t *testing.T) {
	m := answerMsg{Type: "answer", OfferGeneration: 3, SDP: "v=0..."}
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal answer: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("decode marshaled answer: %v", err)
	}
	if got["offer_generation"] != float64(3) {
		t.Errorf("offer_generation = %v, want 3", got["offer_generation"])
	}
}

func TestDecodeRoomSnapshot(t *testing.T) {
	raw := []byte(`{"type":"room_snapshot","room":"1","self_peer_id":2,"participant_count":1,` +
		`"members":[{"peer_id":3,"user_id":"999001","metadata":"  Nguyễn Văn A  ;https://cdn.example/avatar.png","role":"speaker",` +
		`"ufrag":"abc","mid_audio":3,"mid_video":4,"mid_screen":5}]}`)
	var m roomSnapshotMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode room_snapshot: %v", err)
	}
	if m.SelfPeerID != 2 || m.ParticipantCount != 1 {
		t.Errorf("SelfPeerID/ParticipantCount = %d/%d", m.SelfPeerID, m.ParticipantCount)
	}
	if len(m.Members) != 1 {
		t.Fatalf("Members = %+v", m.Members)
	}
	want := Member{PeerID: 3, UserID: 999001, Metadata: "  Nguyễn Văn A  ;https://cdn.example/avatar.png", Role: "speaker", Ufrag: "abc", MidAudio: 3, MidVideo: 4, MidScreen: 5}
	if m.Members[0] != want {
		t.Errorf("Members[0] = %+v, want %+v", m.Members[0], want)
	}
	name, avatar := ParseMemberMetadata(m.Members[0].Metadata)
	if name != "Nguyễn Văn A" || avatar != "https://cdn.example/avatar.png" {
		t.Errorf("parsed metadata = (%q, %q)", name, avatar)
	}
}

// TestDecodeRoomSnapshotWithSlotInfo covers the fixed-pool fields
// (mezon-sfu PR #475): slot/assignment_generation are bare numbers, siblings
// of mid_audio/video/screen, present on room_snapshot's members even though
// TestDecodeRoomSnapshot's payload (the non-fixed-pool shape, where
// mezon-sfu still sends assignment_generation:0 per membership_recipient_valid's
// sentinel) predates PR #475 and deliberately omits them to check the zero
// default still decodes safely.
func TestDecodeRoomSnapshotWithSlotInfo(t *testing.T) {
	raw := []byte(`{"type":"room_snapshot","room":"1","self_peer_id":2,"participant_count":1,` +
		`"members":[{"peer_id":3,"user_id":"999001","metadata":"","role":"speaker",` +
		`"ufrag":"abc","mid_audio":9,"mid_video":10,"mid_screen":11,"slot":2,"assignment_generation":7}]}`)
	var m roomSnapshotMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode room_snapshot: %v", err)
	}
	if len(m.Members) != 1 {
		t.Fatalf("Members = %+v", m.Members)
	}
	got := m.Members[0]
	if got.Slot != 2 || got.AssignmentGeneration != 7 {
		t.Errorf("Members[0] slot/assignment_generation = %d/%d, want 2/7", got.Slot, got.AssignmentGeneration)
	}
}

func TestDecodePeerJoined(t *testing.T) {
	raw := []byte(`{"type":"peer_joined","participant_count":2,"peer":{"peer_id":3,"user_id":"999001",` +
		`"metadata":"  Linh Trần  ","role":"speaker","ufrag":"abc","mid_audio":3,"mid_video":4,"mid_screen":5}}`)
	var m peerJoinedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_joined: %v", err)
	}
	if m.ParticipantCount != 2 || m.Peer.UserID != 999001 || m.Peer.MidScreen != 5 {
		t.Errorf("m = %+v", m)
	}
	name, avatar := ParseMemberMetadata(m.Peer.Metadata)
	if name != "Linh Trần" || avatar != "" {
		t.Errorf("parsed metadata = (%q, %q)", name, avatar)
	}
}

// Payload copied verbatim from mezon-sfu's flush_slot_events snprintf
// format (src/protocol/signaling/signaling.c), same rationale as this
// file's other decode tests: peer_id/mid_audio/mid_video/mid_screen are
// bare numbers, user_id is quoted.
func TestDecodeSlotAssigned(t *testing.T) {
	raw := []byte(`{"type":"slot_assigned","slot":2,"generation":7,"peer_id":10,"user_id":"999001",` +
		`"mid_audio":9,"mid_video":10,"mid_screen":11,"audio_active":true,"video_active":false,"screen_active":false}`)
	var m slotAssignedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode slot_assigned: %v", err)
	}
	want := slotAssignedMsg{
		Type: "slot_assigned", Slot: 2, Generation: 7, PeerID: 10, UserID: 999001,
		MidAudio: 9, MidVideo: 10, MidScreen: 11, AudioActive: true,
	}
	if m != want {
		t.Errorf("m = %+v, want %+v", m, want)
	}
}

func TestDecodeSlotReleased(t *testing.T) {
	raw := []byte(`{"type":"slot_released","slot":2,"generation":7}`)
	var m slotReleasedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode slot_released: %v", err)
	}
	if m.Slot != 2 || m.Generation != 7 {
		t.Errorf("m = %+v", m)
	}
}

func TestDecodeDecoupledSlotMessages(t *testing.T) {
	assignedRaw := []byte(`{"type":"slot_assigned","kind":"audio","slot":0,"generation":12,"mid":3,` +
		`"peer_id":26,"user_id":"1789645245634318336","active":true}`)
	var assigned slotAssignedMsg
	if err := json.Unmarshal(assignedRaw, &assigned); err != nil {
		t.Fatalf("decode slot_assigned: %v", err)
	}
	if assigned.Kind != MediaKindAudio || assigned.Mid != 3 || !assigned.Active ||
		assigned.PeerID != 26 || assigned.UserID != 1789645245634318336 {
		t.Fatalf("assigned = %+v", assigned)
	}

	releasedRaw := []byte(`{"type":"slot_released","kind":"audio","slot":0,"generation":12}`)
	var released slotReleasedMsg
	if err := json.Unmarshal(releasedRaw, &released); err != nil {
		t.Fatalf("decode slot_released: %v", err)
	}
	if released.Kind != MediaKindAudio || released.Slot != 0 || released.Generation != 12 {
		t.Fatalf("released = %+v", released)
	}
}

func TestDecodePeerLeft(t *testing.T) {
	raw := []byte(`{"type":"peer_left","participant_count":1,"ufrag":"abc","user_id":"999001",` +
		`"peer_id":3,"mid_audio":3,"mid_video":4,"mid_screen":5}`)
	var m peerLeftMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_left: %v", err)
	}
	if m.UserID != 999001 || m.PeerID != 3 || m.MidAudio != 3 {
		t.Errorf("m = %+v", m)
	}
}

func TestDecodeDecoupledMemberAndPeerLeft(t *testing.T) {
	raw := []byte(`{"type":"room_snapshot","self_peer_id":2,"participant_count":2,"members":[{` +
		`"peer_id":25,"user_id":"1782991817428439040","metadata":"A","role":"speaker","is_mute":true,` +
		`"camera_requested":false,"camera_active":false,"screen_requested":true,"screen_active":true,` +
		`"ufrag":"1ojr","media_slots":{"audio":null,"camera":null,"screen":{"slot":0,"generation":3,"mid":15}}}]}`)
	var snapshot roomSnapshotMsg
	if err := json.Unmarshal(raw, &snapshot); err != nil {
		t.Fatalf("decode room_snapshot: %v", err)
	}
	m := snapshot.Members[0]
	if !m.IsMute || !m.ScreenRequested || !m.ScreenActive || m.MediaSlots.Screen == nil || m.MediaSlots.Screen.Mid != 15 {
		t.Fatalf("member = %+v", m)
	}

	leftRaw := []byte(`{"type":"peer_left","participant_count":1,"peer_id":25,"user_id":"1782991817428439040",` +
		`"media_slots":{"audio":{"slot":1,"generation":8,"mid":4},"camera":null,"screen":null}}`)
	var left peerLeftMsg
	if err := json.Unmarshal(leftRaw, &left); err != nil {
		t.Fatalf("decode peer_left: %v", err)
	}
	if left.MediaSlots.Audio == nil || left.MediaSlots.Audio.Generation != 8 {
		t.Fatalf("peer_left = %+v", left)
	}
}

// TestEncodeSendMessageFrame locks down the send_message wire shape: the
// outer frame is {"type":"send_message","message":"<string>"} and that
// string is itself a JSON RoomMessage the receiving side decodes back --
// keep symmetric with the "room_message" dispatch case.
func TestEncodeSendMessageFrame(t *testing.T) {
	frame, err := encodeSendMessageFrame(RoomMessage{
		ID:        "900000001",
		Name:      "KOMU Agent",
		Avatar:    "https://cdn.example/komu.png",
		Timestamp: 1757486400000,
		Content:   "  hello room  ",
	})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if frame.Type != "send_message" {
		t.Errorf("Type = %q, want send_message", frame.Type)
	}

	var inner RoomMessage
	if err := json.Unmarshal([]byte(frame.Message), &inner); err != nil {
		t.Fatalf("message field is not a JSON RoomMessage: %v (%q)", err, frame.Message)
	}
	want := RoomMessage{
		ID:        "900000001",
		Name:      "KOMU Agent",
		Avatar:    "https://cdn.example/komu.png",
		Timestamp: 1757486400000,
		Content:   "hello room", // trimmed
	}
	if inner != want {
		t.Errorf("decoded inner = %+v, want %+v", inner, want)
	}
}

func TestEncodeSendMessageFrameRejects(t *testing.T) {
	if _, err := encodeSendMessageFrame(RoomMessage{Content: "   "}); err == nil {
		t.Error("empty content: want error, got nil")
	}
	big := make([]byte, maxRoomMessageBytes+1)
	for i := range big {
		big[i] = 'a'
	}
	if _, err := encodeSendMessageFrame(RoomMessage{Content: string(big)}); err == nil {
		t.Error("over-long content: want error, got nil")
	}
}

func TestDecodePeerUpdated(t *testing.T) {
	raw := []byte(`{"type":"peer_updated","peer":{"peer_id":3,"user_id":"999001","metadata":"  Đổi tên  ;https://cdn.example/avatar;v=2","role":"audience"}}`)
	var m peerUpdatedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_updated: %v", err)
	}
	if m.Peer.UserID != 999001 || m.Peer.Role != "audience" {
		t.Errorf("m = %+v", m)
	}
	name, avatar := ParseMemberMetadata(m.Peer.Metadata)
	if name != "Đổi tên" || avatar != "https://cdn.example/avatar;v=2" {
		t.Errorf("parsed metadata = (%q, %q)", name, avatar)
	}
}

func TestParseMemberMetadata(t *testing.T) {
	tests := []struct {
		name        string
		metadata    string
		displayName string
		avatarURL   string
	}{
		{name: "empty"},
		{name: "empty name and avatar", metadata: ";"},
		{name: "empty name", metadata: "  ;https://cdn.example/avatar.png", avatarURL: "https://cdn.example/avatar.png"},
		{name: "no avatar separator", metadata: "  Nguyễn Văn A  ", displayName: "Nguyễn Văn A"},
		{name: "empty avatar", metadata: "  Nguyễn Văn A  ;", displayName: "Nguyễn Văn A"},
		{name: "first separator only", metadata: " A ;https://cdn.example/avatar;v=2", displayName: "A", avatarURL: "https://cdn.example/avatar;v=2"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			name, avatar := ParseMemberMetadata(tt.metadata)
			if name != tt.displayName || avatar != tt.avatarURL {
				t.Errorf("ParseMemberMetadata(%q) = (%q, %q), want (%q, %q)", tt.metadata, name, avatar, tt.displayName, tt.avatarURL)
			}
		})
	}
}
