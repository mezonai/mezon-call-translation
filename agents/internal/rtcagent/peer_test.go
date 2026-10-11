package rtcagent

import (
	"sync"
	"testing"
	"time"

	"github.com/pion/webrtc/v4"

	"github.com/mezonai/mezon-call-translation/agents/internal/signaling"
)

// newTestPeerAgent builds a PeerAgent with no ICE servers/publish track --
// enough to exercise the mid/slot bookkeeping below without ever dialing
// anything (NewPeerConnection is local-only until SetRemoteDescription).
func newTestPeerAgent(t *testing.T) *PeerAgent {
	t.Helper()
	a, err := New(nil, nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = a.Close() })
	return a
}

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

// TestApplySlotAssignedSeedsCurrentInfo covers the mezon-sfu PR #475 "fixed
// pool" path: slot_assigned must become the live mid -> identity mapping
// readLoop consults, with no OnTrack ever needed to seed it (the slot event
// legitimately arrives first -- see ApplySlotAssigned's doc).
func TestApplySlotAssignedSeedsCurrentInfo(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 999001,
		MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})

	info, ok := a.currentInfo("9")
	if !ok {
		t.Fatal("currentInfo(mid_audio) not found after ApplySlotAssigned")
	}
	want := TrackInfo{Mid: "9", UserID: 999001, PeerID: 10, Kind: KindAudio}
	if info != want {
		t.Errorf("currentInfo(9) = %+v, want %+v", info, want)
	}

	if info, ok := a.currentInfo("10"); !ok || info.Kind != KindCamera || info.PeerID != 10 {
		t.Errorf("currentInfo(mid_video) = %+v, ok=%v", info, ok)
	}
	if info, ok := a.currentInfo("11"); !ok || info.Kind != KindScreen || info.PeerID != 10 {
		t.Errorf("currentInfo(mid_screen) = %+v, ok=%v", info, ok)
	}
}

// TestApplySlotAssignedReassignsSlotToNewPeer: the whole point of this
// mechanism -- the SAME slot (and so the same mids/SSRCs) handed to a
// different peer later must flip currentInfo over to the new occupant, with
// no track restart or OnTrack involved.
func TestApplySlotAssignedReassignsSlotToNewPeer(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 8, PeerID: 20, UserID: 222, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})

	info, ok := a.currentInfo("9")
	if !ok || info.PeerID != 20 || info.UserID != 222 {
		t.Errorf("currentInfo(9) after reassignment = %+v, ok=%v, want peer_id=20 user_id=222", info, ok)
	}
}

// TestApplySlotReleasedClearsMapping: once a slot is released, its mids
// must stop resolving to the old occupant -- readLoop then drops packets
// for that mid (via currentInfo's ok=false) rather than keep attributing
// stray/trailing RTP to whoever used to be there.
func TestApplySlotReleasedClearsMapping(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotReleased(signaling.SlotReleasedEvent{Slot: 2, Generation: 7})

	for _, mid := range []string{"9", "10", "11"} {
		if _, ok := a.currentInfo(mid); ok {
			t.Errorf("currentInfo(%s) still present after ApplySlotReleased", mid)
		}
	}
}

// TestApplySlotReleasedIgnoresStaleGeneration: mezon-sfu coalesces a
// release immediately followed by a reassignment of the same slot into a
// single slot_assigned before ever sending it (flush_slot_events), so a
// release for a generation we've already moved past must be a no-op --
// otherwise it would wrongly clear a mapping a newer slot_assigned already
// corrected.
func TestApplySlotReleasedIgnoresStaleGeneration(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 8, PeerID: 20, UserID: 222, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	// Stale: generation 7 was superseded by 8 above.
	a.ApplySlotReleased(signaling.SlotReleasedEvent{Slot: 2, Generation: 7})

	info, ok := a.currentInfo("9")
	if !ok || info.PeerID != 20 {
		t.Errorf("currentInfo(9) after stale release = %+v, ok=%v, want peer_id=20 still present", info, ok)
	}
}

// TestApplySlotReleasedFinalizesAudioTrack covers the self-review finding
// that a released-but-not-yet-reassigned fixed-pool slot must not leave
// audiopipeline.Bridge's session for that mid running forever (padding
// silence under the departed peer's identity indefinitely): ApplySlotReleased
// must fire OnTrackEnded for the audio mid immediately, not wait on a packet
// that may never arrive.
//
// OnTrackEnded runs in its own goroutine now (see ApplySlotReleased's doc on
// why: avoiding blocking signaling.Client's read loop on a slow
// record-service close), so this polls briefly instead of asserting
// synchronously after the call returns.
func TestApplySlotReleasedFinalizesAudioTrack(t *testing.T) {
	a := newTestPeerAgent(t)

	var mu sync.Mutex
	var ended []TrackInfo
	a.OnTrackEnded = func(info TrackInfo) {
		mu.Lock()
		defer mu.Unlock()
		ended = append(ended, info)
	}

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotReleased(signaling.SlotReleasedEvent{Slot: 2, Generation: 7})

	want := TrackInfo{Mid: "9", UserID: 111, PeerID: 10, Kind: KindAudio}
	deadline := time.Now().Add(2 * time.Second)
	for {
		mu.Lock()
		got := len(ended)
		var last TrackInfo
		if got > 0 {
			last = ended[0]
		}
		mu.Unlock()
		if got == 1 {
			if last != want {
				t.Errorf("OnTrackEnded info = %+v, want %+v", last, want)
			}
			break
		}
		if got > 1 || time.Now().After(deadline) {
			t.Fatalf("OnTrackEnded called %d times within deadline, want 1", got)
		}
		time.Sleep(time.Millisecond)
	}
}

// TestApplySlotReleasedStaleGenerationDoesNotFinalize: a stale/superseded
// release (see ApplySlotReleasedIgnoresStaleGeneration) must not finalize the
// new occupant's track either.
func TestApplySlotReleasedStaleGenerationDoesNotFinalize(t *testing.T) {
	a := newTestPeerAgent(t)

	var mu sync.Mutex
	var ended []TrackInfo
	a.OnTrackEnded = func(info TrackInfo) {
		mu.Lock()
		defer mu.Unlock()
		ended = append(ended, info)
	}

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 8, PeerID: 20, UserID: 222, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	a.ApplySlotReleased(signaling.SlotReleasedEvent{Slot: 2, Generation: 7})

	// Stale release is a no-op synchronously (no goroutine ever spawned for
	// it), so there's nothing to poll for -- just give any (incorrect)
	// spawned goroutine a chance to run before checking.
	time.Sleep(20 * time.Millisecond)
	mu.Lock()
	defer mu.Unlock()
	if len(ended) != 0 {
		t.Errorf("OnTrackEnded called %d times on a stale release, want 0: %+v", len(ended), ended)
	}
}

// TestApplyMemberSlotBootstrapsFromRoster covers the join/reconnect gap: an
// already-occupied fixed-pool slot (learned via room_snapshot/peer_joined's
// Member fields, not a slot_assigned this agent witnessed) must still
// resolve correctly for readLoop/currentInfo -- see ApplyMemberSlot's doc.
func TestApplyMemberSlotBootstrapsFromRoster(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplyMemberSlot(signaling.Member{
		PeerID: 10, UserID: 111, Slot: 2, AssignmentGeneration: 7,
		MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})

	info, ok := a.currentInfo("9")
	want := TrackInfo{Mid: "9", UserID: 111, PeerID: 10, Kind: KindAudio}
	if !ok || info != want {
		t.Errorf("currentInfo(9) = %+v, ok=%v, want %+v, ok=true", info, ok, want)
	}

	// A later real slot_released for the same (slot, generation) must still
	// clear it -- confirms the bootstrap wrote a.slots too, not just
	// midTable, so generation-matching keeps working.
	a.ApplySlotReleased(signaling.SlotReleasedEvent{Slot: 2, Generation: 7})
	if _, ok := a.currentInfo("9"); ok {
		t.Error("currentInfo(9) still present after releasing a bootstrapped slot")
	}
}

// TestApplyMemberSlotIgnoresZeroGeneration covers both the non-fixed-pool
// case (AssignmentGeneration is always 0, mezon-sfu's own "no real
// assignment" sentinel -- see Member's doc) and peer_updated's Member (which
// never carries these fields at all, so they decode as zero) -- neither
// must write a bogus a.slots[0] entry.
func TestApplyMemberSlotIgnoresZeroGeneration(t *testing.T) {
	a := newTestPeerAgent(t)

	a.ApplyMemberSlot(signaling.Member{
		PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
		// Slot/AssignmentGeneration left zero, as for non-fixed-pool rooms
		// and peer_updated.
	})

	if _, ok := a.currentInfo("9"); ok {
		t.Error("currentInfo(9) populated from a zero-generation member, want no-op")
	}
}

// TestFinalInfoFallsBackToMidWhenUnowned covers readLoop's end-of-track call:
// if a mid's slot was released and never reassigned before the underlying
// track itself ends (PeerConnection teardown), finalInfo must still carry the
// real mid rather than the zero value -- otherwise audiopipeline.Bridge's
// HandleTrackEnded (keyed purely by Mid) silently misses the session and
// never closes its record-service stream.
func TestFinalInfoFallsBackToMidWhenUnowned(t *testing.T) {
	a := newTestPeerAgent(t)

	got := a.finalInfo("9", KindAudio)
	want := TrackInfo{Mid: "9", Kind: KindAudio}
	if got != want {
		t.Errorf("finalInfo(unowned mid) = %+v, want %+v", got, want)
	}

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	got = a.finalInfo("9", KindAudio)
	want = TrackInfo{Mid: "9", UserID: 111, PeerID: 10, Kind: KindAudio}
	if got != want {
		t.Errorf("finalInfo(owned mid) = %+v, want %+v", got, want)
	}
}

// TestHandleTrackPrefersExistingSlotAssignment: if ApplySlotAssigned already
// populated a mid (the common ordering in fixed-pool mode -- see
// ApplySlotAssigned's doc on why the slot event usually arrives before the
// first RTP packet), handleTrack's own parseMsid-derived guess for that same
// mid must not clobber it.
func TestHandleTrackPrefersExistingSlotAssignment(t *testing.T) {
	a := newTestPeerAgent(t)
	const mid = "9"

	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})

	// Simulate handleTrack's seeding logic directly (exercising the real
	// pion OnTrack path needs a live connection) with a msid that disagrees
	// with the slot assignment, as would happen if the SDP's placeholder
	// msid for an unoccupied slot differs from whoever mezon-sfu has
	// already told us owns it.
	a.mu.Lock()
	info, known := a.midTable[mid]
	if !known {
		info = TrackInfo{Mid: mid, UserID: 999, PeerID: 99, Kind: KindAudio}
		a.midTable[mid] = info
	}
	a.mu.Unlock()

	if !known {
		t.Fatal("expected ApplySlotAssigned to have already seeded this mid")
	}
	if info.PeerID != 10 || info.UserID != 111 {
		t.Errorf("handleTrack's seeding logic used %+v, want the slot_assigned identity (peer_id=10 user_id=111)", info)
	}
}
