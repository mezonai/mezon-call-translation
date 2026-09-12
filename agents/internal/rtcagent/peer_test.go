package rtcagent

import (
	"testing"

	"github.com/pion/webrtc/v4"

	"github.com/mezonai/mezon-call-translation/agents/internal/signaling"
)

// Regression test for the 2026-08-19 crash: a nil *webrtc.TrackLocalStaticSample
// passed as webrtc.TrackLocal must be recognized as nil, not treated as a
// live track (which would panic inside pion's AddTrack).
func TestIsNilTrack(t *testing.T) {
	var nilConcrete *webrtc.TrackLocalStaticSample
	var wrapped webrtc.TrackLocal = nilConcrete // the trap: this interface is != nil

	if !isNilTrack(wrapped) {
		t.Error("isNilTrack(nil concrete pointer wrapped in interface) = false, want true")
	}
	if !isNilTrack(nil) {
		t.Error("isNilTrack(nil interface) = false, want true")
	}

	track, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2},
		"test", "test",
	)
	if err != nil {
		t.Fatalf("NewTrackLocalStaticSample: %v", err)
	}
	if isNilTrack(track) {
		t.Error("isNilTrack(real track) = true, want false")
	}
}

// Regression test for the 2026-08-20 fix: a stun:/stuns: entry (mezon-sfu
// always hands back itself, which can never answer a gathering-phase STUN
// request -- see convertICEServers' doc) must be dropped, while turn:/turns:
// entries -- and everything else about the server, like credentials -- pass
// through unchanged.
func TestConvertICEServersDropsSTUN(t *testing.T) {
	in := []signaling.ICEServer{
		{URLs: "stun:127.0.0.1:7000"},
		{URLs: "stuns:example.com:5349"},
		{URLs: "turn:127.0.0.1:7000", Username: "u", Credential: "p"},
	}

	out := convertICEServers(in)

	if len(out) != 1 {
		t.Fatalf("convertICEServers() returned %d servers, want 1 (turn: only): %+v", len(out), out)
	}
	if out[0].URLs[0] != "turn:127.0.0.1:7000" || out[0].Username != "u" || out[0].Credential != "p" {
		t.Errorf("convertICEServers() turn: entry = %+v, want URLs=[turn:127.0.0.1:7000] Username=u Credential=p", out[0])
	}
}
