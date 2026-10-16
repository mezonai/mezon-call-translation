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
	a, err := New(nil, nil, signaling.MediaPoolConfig{})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = a.Close() })
	return a
}

func newDecoupledTestPeerAgent(t *testing.T) *PeerAgent {
	t.Helper()
	a, err := New(nil, nil, signaling.MediaPoolConfig{
		Version: 1, Mode: signaling.MediaPoolModeDecoupled,
		MaxAudioSlots: 6, MaxCameraSlots: 6, MaxScreenSlots: 6,
	})
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

// TestParseMsidRejectsFixedPoolSlotFormat pins down the production bug
// (2026-10-16): mezon-sfu's fixed-pool SDP (PR #475, append_fixed_slot_audio/
// video in sdp.c) sets every remote slot's msid to "slot-<n>", never
// "u<user_id>-p<peer_id>" -- so parseMsid must (and does) fail on it. This is
// not a hypothetical edge case: it's every single remote track, every time,
// whenever mezon-sfu runs with max_active_speakers > 0 -- which is exactly
// why handleTrack must never let a parseMsid failure short-circuit before
// checking the slot_assigned-seeded midTable (see TestHandleTrackPrefers
// ExistingSlotAssignment and the fallback test below).
func TestParseMsidRejectsFixedPoolSlotFormat(t *testing.T) {
	if _, _, ok := parseMsid("slot-2"); ok {
		t.Error("parseMsid(\"slot-2\") = ok, want a parse failure (fixed-pool msid carries no user/peer id)")
	}
}

// TestHandleTrackSeedsPlaceholderWhenNeitherSlotAssignedNorParseableMsid
// covers the fallback branch for the rare race where OnTrack's first RTP
// packet beats the slot_assigned signaling message: handleTrack must not
// drop the track outright (OnTrack only ever fires once, so that would lose
// this mid's audio for the track's entire lifetime) -- it seeds a bare
// Mid/Kind placeholder and lets ApplySlotAssigned correct it once it
// arrives, same as finalInfo's fallback (TestFinalInfoFallsBackToMidWhenUnowned).
func TestHandleTrackSeedsPlaceholderWhenNeitherSlotAssignedNorParseableMsid(t *testing.T) {
	a := newTestPeerAgent(t)
	const mid = "9"

	// Simulate handleTrack's fallback logic directly, same rationale as
	// TestHandleTrackPrefersExistingSlotAssignment: a live pion OnTrack
	// needs a real connection, so the inline seeding logic is exercised
	// here instead.
	a.mu.Lock()
	info, known := a.midTable[mid]
	if !known {
		if _, _, ok := parseMsid("slot-2"); ok {
			t.Fatal("parseMsid unexpectedly succeeded on a fixed-pool msid")
		}
		info = TrackInfo{Mid: mid, Kind: KindAudio}
		a.midTable[mid] = info
	}
	a.mu.Unlock()

	if known {
		t.Fatal("expected no slot_assigned seed yet")
	}
	if info != (TrackInfo{Mid: mid, Kind: KindAudio}) {
		t.Errorf("placeholder info = %+v, want {Mid:%q Kind:%v}", info, mid, KindAudio)
	}

	// Once slot_assigned arrives, currentInfo must reflect the real
	// identity -- this is what lets bridge.sessionFor notice the PeerID
	// mismatch against the placeholder session and finalize/recreate it.
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot: 2, Generation: 7, PeerID: 10, UserID: 111, MidAudio: 9, MidVideo: 10, MidScreen: 11,
	})
	got, ok := a.currentInfo(mid)
	if !ok || got.PeerID != 10 || got.UserID != 111 {
		t.Errorf("currentInfo after late slot_assigned = (%+v, %v), want peer_id=10 user_id=111", got, ok)
	}
}

func TestDecoupledMIDLayout(t *testing.T) {
	a := newDecoupledTestPeerAgent(t)
	tests := []struct {
		mid  int
		kind TrackKind
		ok   bool
	}{
		{3, KindAudio, true}, {8, KindAudio, true},
		{9, KindCamera, true}, {14, KindCamera, true},
		{15, KindScreen, true}, {20, KindScreen, true},
		{2, "", false}, {21, "", false},
	}
	for _, tt := range tests {
		kind, ok := a.trackKindForMid(tt.mid)
		if kind != tt.kind || ok != tt.ok {
			t.Errorf("trackKindForMid(%d) = (%q,%v), want (%q,%v)", tt.mid, kind, ok, tt.kind, tt.ok)
		}
	}
}

func TestDecoupledSameSlotNumberIsIndependentByKind(t *testing.T) {
	a := newDecoupledTestPeerAgent(t)
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Kind: signaling.MediaKindAudio, Slot: 0, Generation: 1, Mid: 3,
		PeerID: 10, UserID: 111, Active: true,
	})
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Kind: signaling.MediaKindScreen, Slot: 0, Generation: 4, Mid: 15,
		PeerID: 20, UserID: 222, Active: false,
	})

	if info, ok := a.currentAudioInfo("3"); !ok || info.PeerID != 10 {
		t.Fatalf("audio slot owner = (%+v,%v), want peer 10", info, ok)
	}
	if info, ok := a.currentInfo("15"); !ok || info.PeerID != 20 || info.Kind != KindScreen {
		t.Fatalf("screen slot owner = (%+v,%v), want peer 20", info, ok)
	}

	a.ApplySlotReleased(signaling.SlotReleasedEvent{
		Kind: signaling.MediaKindAudio, Slot: 0, Generation: 1,
	})
	if _, ok := a.currentInfo("3"); ok {
		t.Error("audio MID still owned after audio release")
	}
	if info, ok := a.currentInfo("15"); !ok || info.PeerID != 20 {
		t.Errorf("audio release disturbed screen owner: (%+v,%v)", info, ok)
	}
}

func TestDecoupledGenerationRulesAndReleaseTombstone(t *testing.T) {
	a := newDecoupledTestPeerAgent(t)
	assign := func(generation uint64, peerID uint64, userID int64, active bool) {
		a.ApplySlotAssigned(signaling.SlotAssignedEvent{
			Kind: signaling.MediaKindAudio, Slot: 1, Generation: generation, Mid: 4,
			PeerID: peerID, UserID: userID, Active: active,
		})
	}

	assign(12, 10, 111, true)
	a.ApplySlotReleased(signaling.SlotReleasedEvent{
		Kind: signaling.MediaKindAudio, Slot: 1, Generation: 12,
	})
	assign(12, 10, 111, true) // same-generation assignment after release
	assign(11, 20, 222, true) // older assignment
	if _, ok := a.currentInfo("4"); ok {
		t.Fatal("released generation was resurrected by stale assignment")
	}

	assign(13, 20, 222, false)
	if info, ok := a.currentInfo("4"); !ok || info.PeerID != 20 {
		t.Fatalf("new generation owner = (%+v,%v), want peer 20", info, ok)
	}
	if _, ok := a.currentAudioInfo("4"); ok {
		t.Fatal("inactive audio assignment was treated as playable")
	}
	a.ApplyMemberSlot(signaling.Member{
		PeerID: 20, UserID: 222,
		MediaSlots: signaling.MediaSlots{Audio: &signaling.MediaSlot{Slot: 1, Generation: 13, Mid: 4}},
	})
	if _, ok := a.currentAudioInfo("4"); ok {
		t.Fatal("same-generation membership replay overwrote live inactive state")
	}

	assign(13, 20, 222, true) // same peer/generation may update active
	if info, ok := a.currentAudioInfo("4"); !ok || info.PeerID != 20 {
		t.Fatalf("same-generation active update = (%+v,%v), want active peer 20", info, ok)
	}
	assign(13, 30, 333, true) // same generation, different peer is invalid
	if info, _ := a.currentInfo("4"); info.PeerID != 20 {
		t.Fatalf("same-generation different peer replaced owner: %+v", info)
	}
}

func TestDecoupledMembershipAndPeerLeft(t *testing.T) {
	a := newDecoupledTestPeerAgent(t)
	a.ApplyMemberSlot(signaling.Member{
		PeerID: 25, UserID: 111,
		MediaSlots: signaling.MediaSlots{
			Audio:  &signaling.MediaSlot{Slot: 1, Generation: 8, Mid: 4},
			Screen: &signaling.MediaSlot{Slot: 0, Generation: 3, Mid: 15},
		},
	})
	if info, ok := a.currentAudioInfo("4"); !ok || info.PeerID != 25 {
		t.Fatalf("membership audio = (%+v,%v), want peer 25", info, ok)
	}

	a.ApplyPeerLeft(25, signaling.MediaSlots{}) // defensive release-by-peer path
	if _, ok := a.currentInfo("4"); ok {
		t.Error("peer_left left audio MID owned")
	}
	if _, ok := a.currentInfo("15"); ok {
		t.Error("peer_left left screen MID owned")
	}
}

func TestDecoupledRejectsMIDThatDoesNotMatchLayout(t *testing.T) {
	a := newDecoupledTestPeerAgent(t)
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Kind: signaling.MediaKindAudio, Slot: 0, Generation: 1, Mid: 15,
		PeerID: 10, UserID: 111, Active: true,
	})
	if _, ok := a.currentInfo("15"); ok {
		t.Error("accepted audio slot whose MID belongs to the screen block")
	}
}
