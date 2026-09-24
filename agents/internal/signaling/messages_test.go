package signaling

import (
	"encoding/json"
	"testing"
)

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
		`"members":[{"peer_id":3,"user_id":"999001","metadata":"  Nguyễn Văn A  ;https://cdn.example/avatar.png","role":"speaker","is_mute":false,` +
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
	want := Member{PeerID: 3, UserID: 999001, Metadata: "  Nguyễn Văn A  ;https://cdn.example/avatar.png", Role: "speaker", IsMute: false, Ufrag: "abc", MidAudio: 3, MidVideo: 4, MidScreen: 5}
	if m.Members[0] != want {
		t.Errorf("Members[0] = %+v, want %+v", m.Members[0], want)
	}
	name, avatar := ParseMemberMetadata(m.Members[0].Metadata)
	if name != "Nguyễn Văn A" || avatar != "https://cdn.example/avatar.png" {
		t.Errorf("parsed metadata = (%q, %q)", name, avatar)
	}
}

func TestDecodePeerJoined(t *testing.T) {
	raw := []byte(`{"type":"peer_joined","participant_count":2,"peer":{"peer_id":3,"user_id":"999001",` +
		`"metadata":"  Linh Trần  ","role":"speaker","is_mute":false,"ufrag":"abc","mid_audio":3,"mid_video":4,"mid_screen":5}}`)
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
	raw := []byte(`{"type":"peer_updated","peer":{"peer_id":3,"user_id":"999001","metadata":"  Đổi tên  ;https://cdn.example/avatar;v=2","role":"audience","is_mute":true}}`)
	var m peerUpdatedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_updated: %v", err)
	}
	if m.Peer.UserID != 999001 || m.Peer.Role != "audience" || !m.Peer.IsMute {
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
