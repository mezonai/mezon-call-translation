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
		`"members":[{"peer_id":3,"user_id":"999001","role":"speaker","is_mute":false,` +
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
	want := Member{PeerID: 3, UserID: 999001, Role: "speaker", IsMute: false, Ufrag: "abc", MidAudio: 3, MidVideo: 4, MidScreen: 5}
	if m.Members[0] != want {
		t.Errorf("Members[0] = %+v, want %+v", m.Members[0], want)
	}
}

func TestDecodePeerJoined(t *testing.T) {
	raw := []byte(`{"type":"peer_joined","participant_count":2,"peer":{"peer_id":3,"user_id":"999001",` +
		`"role":"speaker","is_mute":false,"ufrag":"abc","mid_audio":3,"mid_video":4,"mid_screen":5}}`)
	var m peerJoinedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_joined: %v", err)
	}
	if m.ParticipantCount != 2 || m.Peer.UserID != 999001 || m.Peer.MidScreen != 5 {
		t.Errorf("m = %+v", m)
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

func TestDecodePeerUpdated(t *testing.T) {
	raw := []byte(`{"type":"peer_updated","peer":{"peer_id":3,"user_id":"999001","role":"audience","is_mute":true}}`)
	var m peerUpdatedMsg
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decode peer_updated: %v", err)
	}
	if m.Peer.UserID != 999001 || m.Peer.Role != "audience" || !m.Peer.IsMute {
		t.Errorf("m = %+v", m)
	}
}
