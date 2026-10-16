// Package rtcagent wraps a pion PeerConnection configured to talk to
// mezon-sfu, and maintains the runtime `mid -> {user_id, is_screen}` table
// used to label incoming audio for the record-forwarding path.
//
// Legacy recipe (mezon-sfu-migration-checklist.md D2, 2026-08-16 layout):
//   - Each peer has 3 fixed uplink slots: mid 0/1/2 = audio/camera/screen of
//     THIS agent; remote peers start at mid 3, 3-wide per peer
//     (SFU_REMOTE_MID_BASE=3): mid 3/4/5 = slot #1 audio/camera/screen,
//     mid 6/7/8 = slot #2, etc.
//   - `a=msid:u<user_id>-p<peer_id>` on every section: pion surfaces this as
//     TrackRemote.StreamID(), so user_id/peer_id are read directly off the
//     track when a mid is first seen -- no need to cross-reference
//     room_snapshot/peer_joined for that (we still keep a roster map for
//     role). This is only a one-time *initial* reading, though: in
//     mezon-sfu's "fixed pool" mode (PR #475, server config
//     max_active_speakers > 0) a slot's mid/SSRC can be handed to a
//     different peer later without any SDP renegotiation at all, so the
//     msid alone can go stale for the lifetime of a long-running track --
//     see ApplySlotAssigned's doc for the mid -> identity table this keeps
//     live instead.
//   - mid position (offset 0/1/2 from the peer's 3-wide slot) tells us
//     mic vs camera vs screen without any heuristic.
//
// In decoupled mode, joined's max_*_slots values instead define contiguous
// audio, camera and screen blocks. Ownership is then maintained independently
// by (kind, slot), while the MID/transceiver remains stable across owners.
package rtcagent

import (
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/signaling"
)

// closeTimeout bounds pion's PeerConnection.Close, which has no built-in
// deadline of its own. Local teardown (ICE/DTLS/SRTP), not a call that waits
// on the remote side, so this is purely a defensive ceiling against an
// unexpected hang, not something normal operation should ever approach.
const closeTimeout = 2 * time.Second

// trackCloseGrace bounds how long Close() waits for every readLoop
// goroutine (and its HandleTrackEnded -> recordclient.Forwarder.Close call)
// to finish after pc.Close(). This is genuinely necessary work, not a
// network-defensive timeout to shrink -- see trackWG's doc for what breaks
// without it (record-service silently never gets the tail of a recording).
// A little above recordclient.Forwarder's own closeGrace (2s) so that
// budget gets to run to its own completion rather than being cut off here
// first.
const trackCloseGrace = 3 * time.Second

const remoteMidBase = 3 // mezon-sfu SFU_REMOTE_MID_BASE

// TrackKind classifies a remote uplink slot by its position.
type TrackKind string

const (
	KindAudio  TrackKind = "mic"
	KindCamera TrackKind = "camera"
	KindScreen TrackKind = "screen"
)

// TrackInfo is what internal/audiopipeline needs to label a decoded audio
// frame with the user/track it came from.
type TrackInfo struct {
	Mid             string
	UserID          int64
	PeerID          uint64
	Kind            TrackKind
	BindingRevision uint64
}

var msidRe = regexp.MustCompile(`^u(-?\d+)-p(\d+)$`)

// slotOwner is what ApplySlotReleased needs to find the mids a retiring
// slot's generation last owned (slot_released carries only slot+generation,
// not peer identity or mids -- see signaling.SlotReleasedEvent's doc).
type slotOwner struct {
	generation uint64
	midAudio   uint32
	midVideo   uint32
	midScreen  uint32
}

type poolKey struct {
	kind signaling.MediaKind
	slot uint32
}

// poolBinding is retained after release (assigned=false) so a delayed
// assignment from the same/older generation cannot resurrect stale ownership.
type poolBinding struct {
	generation uint64
	peerID     uint64
	userID     int64
	mid        uint32
	active     bool
	assigned   bool
	revision   uint64
}

// PeerAgent owns one pion PeerConnection for the lifetime of one mezon-sfu
// session (one WS connection worth of ICE/DTLS/SRTP state).
type PeerAgent struct {
	pc *webrtc.PeerConnection

	mu                  sync.Mutex
	midTable            map[string]TrackInfo       // mid -> track info. Live: both handleTrack (OnTrack) and ApplySlotAssigned write it, readLoop reads it fresh per packet -- see ApplySlotAssigned's doc for why a one-time OnTrack snapshot isn't enough.
	roster              map[int64]signaling.Member // user_id -> latest roster entry (role)
	slots               map[uint32]slotOwner       // fixed-pool slot number -> current occupant, keyed to resolve slot_released (which carries only the slot number)
	mediaPool           signaling.MediaPoolConfig
	pool                map[poolKey]poolBinding
	activeMids          map[string]bool
	nextBindingRevision uint64

	// trackWG tracks every readLoop goroutine still running. Close() waits
	// on it (see trackCloseGrace's doc) -- without this, readLoop's
	// HandleTrackEnded callback (which closes this track's record-service
	// forwarder gRPC stream, internal/recordclient.Forwarder.Close) races
	// against cmd/agent/main.go's own `defer recClient.Close()`, which tears
	// down the *shared* gRPC connection all forwarders use. Confirmed
	// 2026-08-20 against a real record-service run: the forwarder's own
	// graceful CloseSend()+wait-for-ack was still in flight when the shared
	// connection got torn down out from under it ("recordclient: read loop
	// stopped err=\"...grpc: the client connection is closing\""), which
	// left record-service's StreamAudio handler suspended mid-`finally` --
	// its own cleanup coroutine never resumed (confirmed via py-spy: every
	// thread idle, nothing running) because grpc.aio never got a clean
	// half-close to react to, only an abrupt full-connection drop -- so the
	// session's buffered audio never got uploaded, forever, even though
	// record-service's own logic is fine on its own.
	trackWG sync.WaitGroup

	// closing is set at the very start of Close(), before pc.Close() and
	// well before trackWG.Wait() -- handleTrack checks it to refuse any
	// track that arrives after shutdown has begun. Needed because Close()
	// only waits up to closeTimeout for pc.Close() to return before moving
	// on regardless (see Close's doc); if pc.Close() is genuinely still
	// hung past that point, pion's internals are still tearing down and can
	// still invoke OnTrack for a track that was mid-negotiation. Without
	// this guard, that late handleTrack's trackWG.Add(1) could race
	// trackWG.Wait() -- sync.WaitGroup's contract requires a positive-delta
	// Add from a zero counter to happen-before any concurrent Wait, and
	// violating it here would silently reopen the exact truncated-recording
	// race trackWG exists to close, for that one straggler track. Plain
	// atomic.Bool rather than folding into mu: read on every OnTrack, so it
	// shouldn't share a lock with anything else handleTrack does.
	closing atomic.Bool

	// OnAudioPacket and OnTrackEnded, if set, are both invoked from the same
	// single goroutine: one track's own read loop (see readLoop). That's
	// deliberate -- it's what lets a consumer (internal/recording.Bridge)
	// keep a per-track decoder/forwarder pair without any locking of its
	// own: create-on-first-packet and close-on-end always happen on that
	// one goroutine, back-to-back, never concurrently with each other for
	// the same track. Different tracks run on different goroutines, so a
	// consumer touching shared state across tracks (e.g. a map keyed by
	// mid) still needs its own lock for that map -- just not per-entry.
	OnAudioPacket func(info TrackInfo, pkt *rtp.Packet)
	// OnTrackEnded fires once, when a track's RTP read loop exits (pion
	// signals the track ended, or the PeerConnection closed).
	OnTrackEnded func(info TrackInfo)
}

// iceMaxBindingRequests raises pion's per-candidate-pair STUN retry budget
// well above its default of 7 (~1.4s at the default 200ms check interval).
//
// [2026-08-20, root-caused against a real mezon-sfu run] mezon-sfu withholds
// any STUN response for a candidate's ufrag until it has finished processing
// this agent's SDP answer over the WS signaling channel
// (signaling.c:1127, "awaits authenticated STUN bind"). Getting that answer
// sent takes ~5s in practice -- see stunGatherTimeout below for why: almost
// all of it is this agent itself sitting in ICE candidate gathering, not
// mezon-sfu doing anything slow. At pion's default of 7 requests, every
// local candidate pair had already been marked CandidatePairStateFailed
// (agent.go:806, pion/ice v4.4.0) by ~1.4s in -- 3.6s before mezon-sfu was
// even ready to answer a single one of them. With every pair permanently
// failed, ICE just sits in "checking" until pion's own failedTimeout
// (disconnectedTimeout+failedTimeout = 30s default) gives up for good --
// matching the exact `checking` -> `failed` timing seen in testing. This is
// real, necessary work (waiting for mezon-sfu's own signaling round trip),
// not a network fault to fail fast on -- so the fix is to make pion patient
// enough to still be retrying once mezon-sfu is ready, not to make
// mezon-sfu faster. 50 requests * 200ms default check interval = ~10s of
// retry per pair, comfortably past the observed ~5s with margin for slower
// runs.
const iceMaxBindingRequests = 50

// stunGatherTimeout replaces pion's default 5s ICE-gathering STUN wait
// (defaultSTUNGatherTimeout, pion/ice agent_config.go) with something much
// shorter -- a safety net, not the primary fix.
//
// [2026-08-20, root-caused] Originally this constant alone was the fix for a
// real ~5s startup delay: mezon-sfu's `joined` response used to hand back
// itself as the STUN server in iceServers, pion would use that during
// gathering to learn its own server-reflexive candidate, and per
// mezon-sfu/src/pipeline/dispatch.c's handle_stun, mezon-sfu withholds
// *every* STUN response, unconditionally, until a signaling route exists
// for that ufrag (see iceMaxBindingRequests' doc) -- which can't happen yet
// at gathering time, so that request could never succeed, not occasionally
// but by construction, on every single join. convertICEServers now filters
// that entry out before it ever reaches pion, so this timeout shouldn't
// normally matter anymore. Kept short (not removed) as defense in depth for
// any other stun:/stuns: entry mezon-sfu might send that turns out to be
// equally unreachable -- this is dead time to fail fast on, not necessary
// work, unlike iceMaxBindingRequests.
const stunGatherTimeout = 500 * time.Millisecond

// New creates the PeerConnection using the ICE servers mezon-sfu returned in
// `joined`. mezon-sfu sends `a=ice-lite` (still true as of the 2026-08-16
// protocol, mezon-sfu/src/protocol/signaling/sdp.c:370) -- pion is a full
// ICE agent by default, which is exactly what's required here. The two
// non-default settings are iceMaxBindingRequests and stunGatherTimeout
// above -- see those constants' docs for why one is extended and the other
// cut down, both driven by the same mezon-sfu STUN-withholding behavior.
//
// publishTrack is nil for an "audience" session (record-only). For a
// "speaker" session (internal/ttsplayer), pass a
// *webrtc.TrackLocalStaticSample here -- it must be attached via AddTrack
// before the first SetRemoteDescription/CreateAnswer (i.e. before the
// caller wires HandleOffer to any incoming offer), because mezon-sfu's own
// uplink audio slot (mid 0, see the package doc) is always the first audio
// m-line in its offer, and pion matches an already-added local track to the
// first compatible unassigned m-line when building the answer -- adding the
// track any later would miss that negotiation window.
func New(iceServers []signaling.ICEServer, publishTrack webrtc.TrackLocal, mediaPool signaling.MediaPoolConfig) (*PeerAgent, error) {
	cfg := webrtc.Configuration{ICEServers: convertICEServers(iceServers)}

	se := webrtc.SettingEngine{}
	se.SetICEMaxBindingRequests(iceMaxBindingRequests)
	se.SetSTUNGatherTimeout(stunGatherTimeout)
	api := webrtc.NewAPI(webrtc.WithSettingEngine(se))

	pc, err := api.NewPeerConnection(cfg)
	if err != nil {
		return nil, fmt.Errorf("rtcagent: new peer connection: %w", err)
	}

	a := &PeerAgent{
		pc:         pc,
		midTable:   make(map[string]TrackInfo),
		roster:     make(map[int64]signaling.Member),
		slots:      make(map[uint32]slotOwner),
		mediaPool:  mediaPool,
		pool:       make(map[poolKey]poolBinding),
		activeMids: make(map[string]bool),
	}

	// isNilTrack, not a plain `publishTrack != nil`: publishTrack is an
	// interface, so a caller passing a nil value through a concrete pointer
	// type (e.g. a `var t *webrtc.TrackLocalStaticSample` left nil) produces
	// a non-nil interface (type set, value nil) -- `!= nil` would then be
	// true and pc.AddTrack would panic inside pion on that nil pointer
	// (confirmed against a real mezon-sfu run, 2026-08-19: SIGSEGV in
	// TrackLocalStaticSample.Kind). cmd/agent's caller already converts
	// carefully to avoid this, but checking it here too means this package
	// doesn't rely on every caller getting that dance right.
	if !isNilTrack(publishTrack) {
		if _, err := pc.AddTrack(publishTrack); err != nil {
			_ = pc.Close()
			return nil, fmt.Errorf("rtcagent: add publish track: %w", err)
		}
	}

	pc.OnICEConnectionStateChange(func(s webrtc.ICEConnectionState) {
		logging.L.Info("rtcagent: ice connection state", "state", s.String())
	})
	pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		logging.L.Info("rtcagent: peer connection state", "state", s.String())
	})
	pc.OnTrack(a.handleTrack)

	return a, nil
}

// isNilTrack reports whether track is nil, seeing through the interface
// wrapping that makes a plain `track != nil` lie for a nil concrete pointer
// (see New's doc). Every real webrtc.TrackLocal implementation (e.g.
// *webrtc.TrackLocalStaticSample) is a pointer type, so reflect.Value.IsNil
// is always valid here.
func isNilTrack(track webrtc.TrackLocal) bool {
	if track == nil {
		return true
	}
	v := reflect.ValueOf(track)
	return v.Kind() == reflect.Pointer && v.IsNil()
}

// convertICEServers drops stun:/stuns: entries and passes through everything
// else (turn:/turns:, if mezon-sfu ever sends one).
//
// [2026-08-20] mezon-sfu's `joined` response hands back itself as the STUN
// server (mezon-sfu/src/protocol/signaling/signaling.c). Configuring that
// with pion would make it send a gathering-phase STUN request to learn its
// own server-reflexive candidate -- but mezon-sfu's handle_stun
// (src/pipeline/dispatch.c) withholds *every* STUN response, unconditionally,
// until a signaling route exists for that ufrag, and no route exists yet at
// gathering time (this agent hasn't sent its SDP answer yet -- gathering is
// what produces it). So this specific request can never succeed, not
// occasionally but by construction, in any environment -- host candidates
// (already directly reachable, since mezon-sfu's own address arrives via the
// SDP offer regardless of ICEServers, not via STUN discovery) are all this
// protocol can ever use for reaching mezon-sfu. Dropping the entry entirely
// avoids waiting on it at all, on top of the stunGatherTimeout safety net
// below for anything unexpected. If mezon-sfu ever starts returning a STUN
// server that ISN'T itself (e.g. real NAT-traversal infra for an agent not
// co-located with mezon-sfu), this filter would also skip that one -- revisit
// then.
func convertICEServers(servers []signaling.ICEServer) []webrtc.ICEServer {
	out := make([]webrtc.ICEServer, 0, len(servers))
	for _, s := range servers {
		if strings.HasPrefix(s.URLs, "stun:") || strings.HasPrefix(s.URLs, "stuns:") {
			continue
		}
		ice := webrtc.ICEServer{URLs: []string{s.URLs}}
		if s.Username != "" || s.Credential != "" {
			ice.Username = s.Username
			ice.Credential = s.Credential
		}
		out = append(out, ice)
	}
	return out
}

// HandleOffer implements signaling.Callbacks.OnOffer: set remote offer,
// create+set local answer, wait for ICE gathering (mezon-sfu does not
// trickle ICE -- mezon-sfu/CLAUDE.md section 4 -- so the answer must carry
// final candidates), and return the answer SDP.
func (a *PeerAgent) HandleOffer(sdp string) (string, error) {
	// DEBUG only (LOG_LEVEL=debug) -- the raw SDP text is long and only
	// useful when actively diagnosing a negotiation/track-binding issue
	// (e.g. mezon-sfu-migration-checklist.md D2's missing a=ssrc problem).
	logging.L.Debug("rtcagent: received offer sdp", "sdp", sdp)

	offer := webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: sdp}
	if err := a.pc.SetRemoteDescription(offer); err != nil {
		return "", fmt.Errorf("rtcagent: set remote description: %w", err)
	}

	answer, err := a.pc.CreateAnswer(nil)
	if err != nil {
		return "", fmt.Errorf("rtcagent: create answer: %w", err)
	}

	gatherComplete := webrtc.GatheringCompletePromise(a.pc)
	if err := a.pc.SetLocalDescription(answer); err != nil {
		return "", fmt.Errorf("rtcagent: set local description: %w", err)
	}
	<-gatherComplete

	local := a.pc.LocalDescription()
	if local == nil {
		return "", fmt.Errorf("rtcagent: no local description after gathering")
	}
	logging.L.Debug("rtcagent: sending answer sdp", "sdp", local.SDP)
	return local.SDP, nil
}

// UpsertRoster records/updates a roster entry (role) for a user_id.
// Wired to signaling.Callbacks.OnRoomSnapshot / OnPeerJoined / OnPeerUpdated.
func (a *PeerAgent) UpsertRoster(m signaling.Member) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.roster[m.UserID] = m
}

// RemovePeer drops the roster entry and any midTable entries for a user_id
// that left. Wired to signaling.Callbacks.OnPeerLeft.
func (a *PeerAgent) RemovePeer(userID int64) {
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.roster, userID)
	for mid, info := range a.midTable {
		if info.UserID == userID {
			delete(a.midTable, mid)
			delete(a.activeMids, mid)
		}
	}
}

// ApplySlotAssigned updates the live mid -> identity mapping when mezon-sfu
// hands a "fixed pool" speaker slot to a (possibly different) peer.
//
// Background (mezon-sfu PR #475, server config max_active_speakers > 0): in
// that mode the SFU pre-allocates a bounded, fixed set of remote slots up
// front -- each with its own permanent SSRC for the whole room's lifetime --
// instead of growing mids per peer as before. Reassigning a slot from one
// peer to another does NOT renegotiate the SDP (mezon-sfu room_media_graph.c:
// `renegotiate = !sfu_session_has_fixed_pool(...)`): the same mid keeps
// streaming RTP on the same SSRC throughout, pion's OnTrack fires exactly
// once for it ever, and parseMsid's one-time reading of the SDP-time msid
// (handleTrack) is only ever correct for whoever happened to occupy the slot
// at that moment. This event is the *only* signal that the occupant changed,
// so it must override parseMsid's value for every packet from here on --
// see readLoop/currentInfoLocked, which re-resolve this per packet rather
// than trusting handleTrack's one-time snapshot.
//
// Wire to signaling.Callbacks.OnSlotAssigned.
func (a *PeerAgent) ApplySlotAssigned(ev signaling.SlotAssignedEvent) {
	if ev.Kind != "" {
		a.applyDecoupledAssignment(ev, false)
		return
	}

	a.mu.Lock()
	defer a.mu.Unlock()

	a.slots[ev.Slot] = slotOwner{
		generation: ev.Generation,
		midAudio:   ev.MidAudio,
		midVideo:   ev.MidVideo,
		midScreen:  ev.MidScreen,
	}

	a.setMidOwnerLocked(ev.MidAudio, ev.UserID, ev.PeerID, KindAudio)
	a.setMidOwnerLocked(ev.MidVideo, ev.UserID, ev.PeerID, KindCamera)
	a.setMidOwnerLocked(ev.MidScreen, ev.UserID, ev.PeerID, KindScreen)
}

// ApplySlotReleased clears the live mid -> identity mapping for a
// fixed-pool slot that no longer has an occupant. Ignored if generation
// doesn't match the assignment currently on record for that slot -- a
// stale/superseded event, since mezon-sfu coalesces a release immediately
// followed by a reassignment of the same slot into a single slot_assigned
// before it's ever sent (flush_slot_events' coalescing), so an out-of-date
// release reaching us would otherwise clear a mapping a newer slot_assigned
// already corrected.
//
// Also finalizes the outgoing peer's audio track immediately via
// OnTrackEnded, rather than waiting for a packet that may never arrive:
// readLoop only re-resolves the mid -> identity mapping (and so only ever
// notices a change) when a new RTP packet shows up for that mid (see
// currentInfo's doc). A slot that sits released with no new occupant for a
// while (room has fewer active speakers than pool slots, or the departing
// peer's connection is already gone) would otherwise leave
// audiopipeline.Bridge's session for this mid running forever -- its
// record-service forwarder and background silence-padding goroutine never
// told to stop, continuing to pad synthetic silence under the departed
// peer's identity indefinitely.
//
// OnTrackEnded is invoked in its own goroutine, not inline: it's wired to
// audiopipeline.Bridge.HandleTrackEnded, which closes the record-service
// forwarder -- recordclient.Forwarder.Close can block up to closeGrace (2s)
// waiting on record-service. ApplySlotReleased itself runs on
// signaling.Client's single read-loop goroutine (Client.Run), which also
// owns replying to the server's keepalive ping (10s interval, 20s idle
// timeout -- see Client.dispatch's "ping" case) and dispatching every other
// signaling message (offers, other slot events); blocking it for up to 2s
// per release, or worse on a burst of several releases while record-service
// is slow, risks tripping that idle timeout and getting the whole session
// disconnected over a transient storage hiccup that has nothing to do with
// the signaling channel itself. This call is also wired to a consumer
// (audiopipeline.Bridge) that already tolerates being invoked from any
// goroutine -- see HandleTrackEnded's doc -- so moving it off this
// goroutine changes no correctness property, only which goroutine can be
// blocked by it. This is the same deliberate, audited exception to the
// "OnAudioPacket/OnTrackEnded only ever called from one track's own read
// loop" invariant as before -- running it on its own goroutine instead of
// the signaling one doesn't change that, only avoids compounding it with a
// second, unrelated goroutine (the signaling read loop) also being blocked.
//
// Wire to signaling.Callbacks.OnSlotReleased.
func (a *PeerAgent) ApplySlotReleased(ev signaling.SlotReleasedEvent) {
	if ev.Kind != "" {
		a.applyDecoupledRelease(ev)
		return
	}

	a.mu.Lock()
	owner, ok := a.slots[ev.Slot]
	if !ok || owner.generation != ev.Generation {
		a.mu.Unlock()
		return
	}
	delete(a.slots, ev.Slot)

	audioInfo, hadAudio := a.clearMidOwnerLocked(owner.midAudio)
	a.clearMidOwnerLocked(owner.midVideo)
	a.clearMidOwnerLocked(owner.midScreen)
	a.mu.Unlock()

	if hadAudio && a.OnTrackEnded != nil {
		go a.OnTrackEnded(audioInfo)
	}
}

// ApplyMemberSlot is the defensive counterpart to ApplySlotAssigned: it
// bootstraps the mid -> identity mapping for a fixed-pool slot that was
// already occupied *before* this agent joined (or reconnected), from a
// roster message's own mid_audio/video/screen + slot/assignment_generation
// fields, rather than relying solely on the live slot_assigned/slot_released
// stream (which only reports *future* changes, not a joiner's view of
// current state) or on parseMsid's one-time SDP reading (which this
// package's doc already flags as potentially stale in fixed-pool mode, and
// whose correctness for an *already-occupied* slot at this agent's own join
// time isn't something this package controls or has verified against
// mezon-sfu's offer-building code -- see the package doc). Cheap insurance
// either way: if parseMsid's reading does turn out to already be correct at
// join time, this is a harmless no-op re-write of the same values.
//
// Gated on AssignmentGeneration != 0, mezon-sfu's own sentinel for "this
// recipient has no real fixed-pool assignment" (signaling.c's
// membership_recipient_valid, see Member's doc) -- not on Slot itself, which
// has no such sentinel value and would otherwise be indistinguishable from a
// legitimate "slot 0". This also means the gate is a safe no-op outside
// fixed-pool mode (AssignmentGeneration is always 0 there) and for
// peer_updated's Member (which never carries these fields on the wire at
// all, so they always decode as zero) -- so this is safe to call
// unconditionally for every roster message, not just ones known to be
// fixed-pool-relevant.
//
// Wire alongside UpsertRoster for room_snapshot and peer_joined members.
// peer_updated carries display state only and must not change slot ownership.
func (a *PeerAgent) ApplyMemberSlot(m signaling.Member) {
	if a.mediaPool.IsDecoupled() {
		a.applyMemberMediaSlots(m)
		return
	}
	if m.AssignmentGeneration == 0 {
		return
	}
	a.ApplySlotAssigned(signaling.SlotAssignedEvent{
		Slot:       m.Slot,
		Generation: m.AssignmentGeneration,
		PeerID:     m.PeerID,
		UserID:     m.UserID,
		MidAudio:   m.MidAudio,
		MidVideo:   m.MidVideo,
		MidScreen:  m.MidScreen,
	})
}

func (a *PeerAgent) applyMemberMediaSlots(m signaling.Member) {
	for _, entry := range []struct {
		kind signaling.MediaKind
		slot *signaling.MediaSlot
	}{
		{signaling.MediaKindAudio, m.MediaSlots.Audio},
		{signaling.MediaKindCamera, m.MediaSlots.Camera},
		{signaling.MediaKindScreen, m.MediaSlots.Screen},
	} {
		if entry.slot == nil {
			continue
		}
		a.applyDecoupledAssignment(signaling.SlotAssignedEvent{
			Kind:       entry.kind,
			Slot:       entry.slot.Slot,
			Generation: entry.slot.Generation,
			Mid:        entry.slot.Mid,
			PeerID:     m.PeerID,
			UserID:     m.UserID,
			Active:     true,
		}, true)
	}
}

// ApplyPeerLeft releases the decoupled bindings explicitly listed by SFU and
// then defensively releases any remaining binding owned by peerID.
func (a *PeerAgent) ApplyPeerLeft(peerID uint64, slots signaling.MediaSlots) {
	if !a.mediaPool.IsDecoupled() {
		return
	}
	for _, entry := range []struct {
		kind signaling.MediaKind
		slot *signaling.MediaSlot
	}{
		{signaling.MediaKindAudio, slots.Audio},
		{signaling.MediaKindCamera, slots.Camera},
		{signaling.MediaKindScreen, slots.Screen},
	} {
		if entry.slot != nil {
			a.applyDecoupledRelease(signaling.SlotReleasedEvent{
				Kind: entry.kind, Slot: entry.slot.Slot, Generation: entry.slot.Generation,
			})
		}
	}

	a.mu.Lock()
	remaining := make([]signaling.SlotReleasedEvent, 0)
	for key, binding := range a.pool {
		if binding.assigned && binding.peerID == peerID {
			remaining = append(remaining, signaling.SlotReleasedEvent{
				Kind: key.kind, Slot: key.slot, Generation: binding.generation,
			})
		}
	}
	a.mu.Unlock()
	for _, ev := range remaining {
		a.applyDecoupledRelease(ev)
	}
}

func (a *PeerAgent) applyDecoupledAssignment(ev signaling.SlotAssignedEvent, fromMembership bool) {
	expectedMid, ok := a.midOf(ev.Kind, ev.Slot)
	if !ok || expectedMid != ev.Mid {
		logging.L.Warn("rtcagent: ignoring invalid decoupled slot assignment",
			"kind", ev.Kind, "slot", ev.Slot, "mid", ev.Mid, "expected_mid", expectedMid)
		return
	}

	key := poolKey{kind: ev.Kind, slot: ev.Slot}
	var ended TrackInfo
	var shouldEnd bool
	a.mu.Lock()
	prev, exists := a.pool[key]
	if exists && prev.generation > ev.Generation {
		a.mu.Unlock()
		return
	}
	if exists && prev.generation == ev.Generation &&
		(fromMembership || !prev.assigned || prev.peerID != ev.PeerID) {
		a.mu.Unlock()
		return
	}

	midKey := strconv.FormatUint(uint64(ev.Mid), 10)
	if ev.Kind == signaling.MediaKindAudio && prev.assigned &&
		(prev.peerID != ev.PeerID || (prev.active && !ev.Active)) {
		ended, shouldEnd = a.midTable[midKey]
	}

	revision := prev.revision
	if !exists || !prev.assigned || prev.generation != ev.Generation ||
		prev.peerID != ev.PeerID || prev.active != ev.Active {
		a.nextBindingRevision++
		revision = a.nextBindingRevision
	}
	a.pool[key] = poolBinding{
		generation: ev.Generation,
		peerID:     ev.PeerID,
		userID:     ev.UserID,
		mid:        ev.Mid,
		active:     ev.Active,
		assigned:   true,
		revision:   revision,
	}
	a.midTable[midKey] = TrackInfo{
		Mid: midKey, UserID: ev.UserID, PeerID: ev.PeerID, Kind: trackKind(ev.Kind), BindingRevision: revision,
	}
	if ev.Kind == signaling.MediaKindAudio {
		a.activeMids[midKey] = ev.Active
	}
	a.mu.Unlock()

	if shouldEnd && a.OnTrackEnded != nil {
		go a.OnTrackEnded(ended)
	}
}

func (a *PeerAgent) applyDecoupledRelease(ev signaling.SlotReleasedEvent) {
	if _, ok := a.midOf(ev.Kind, ev.Slot); !ok {
		logging.L.Warn("rtcagent: ignoring invalid decoupled slot release", "kind", ev.Kind, "slot", ev.Slot)
		return
	}

	key := poolKey{kind: ev.Kind, slot: ev.Slot}
	var ended TrackInfo
	var shouldEnd bool
	a.mu.Lock()
	prev, exists := a.pool[key]
	if exists && prev.generation > ev.Generation {
		a.mu.Unlock()
		return
	}
	if exists && prev.assigned {
		midKey := strconv.FormatUint(uint64(prev.mid), 10)
		ended, shouldEnd = a.midTable[midKey]
		delete(a.midTable, midKey)
		delete(a.activeMids, midKey)
	}
	a.pool[key] = poolBinding{generation: ev.Generation, active: false, assigned: false}
	a.mu.Unlock()

	if ev.Kind == signaling.MediaKindAudio && shouldEnd && a.OnTrackEnded != nil {
		go a.OnTrackEnded(ended)
	}
}

func (a *PeerAgent) midOf(kind signaling.MediaKind, slot uint32) (uint32, bool) {
	if !a.mediaPool.IsDecoupled() || !kind.Valid() {
		return 0, false
	}
	start := uint32(remoteMidBase)
	for _, block := range []struct {
		kind  signaling.MediaKind
		count uint32
	}{
		{signaling.MediaKindAudio, a.mediaPool.MaxAudioSlots},
		{signaling.MediaKindCamera, a.mediaPool.MaxCameraSlots},
		{signaling.MediaKindScreen, a.mediaPool.MaxScreenSlots},
	} {
		if block.kind == kind {
			if slot >= block.count {
				return 0, false
			}
			return start + slot, true
		}
		start += block.count
	}
	return 0, false
}

func trackKind(kind signaling.MediaKind) TrackKind {
	switch kind {
	case signaling.MediaKindAudio:
		return KindAudio
	case signaling.MediaKindCamera:
		return KindCamera
	default:
		return KindScreen
	}
}

func (a *PeerAgent) setMidOwnerLocked(mid uint32, userID int64, peerID uint64, kind TrackKind) {
	if mid == 0 {
		return
	}
	key := strconv.FormatUint(uint64(mid), 10)
	a.midTable[key] = TrackInfo{Mid: key, UserID: userID, PeerID: peerID, Kind: kind}
}

// clearMidOwnerLocked removes mid's entry, returning what was there (if
// anything) so ApplySlotReleased can finalize the outgoing peer's track.
func (a *PeerAgent) clearMidOwnerLocked(mid uint32) (TrackInfo, bool) {
	if mid == 0 {
		return TrackInfo{}, false
	}
	key := strconv.FormatUint(uint64(mid), 10)
	info, ok := a.midTable[key]
	delete(a.midTable, key)
	return info, ok
}

// Close tears down the PeerConnection. Closing the parent WS connection is
// what actually ends the mezon-sfu session server-side; this just releases
// local resources. Bounded by closeTimeout rather than trusting pion to
// always return promptly.
func (a *PeerAgent) Close() error {
	// Must happen before anything else -- see closing's doc for why this
	// needs to be set well ahead of trackWG.Wait() below, not just before it.
	a.closing.Store(true)

	done := make(chan error, 1)
	go func() { done <- a.pc.Close() }()
	var closeErr error
	select {
	case closeErr = <-done:
	case <-time.After(closeTimeout):
		logging.L.Warn("rtcagent: PeerConnection.Close did not return within timeout, continuing shutdown", "timeout", closeTimeout)
	}

	// pc.Close() unblocks every track's ReadRTP with an error, but each
	// track's own readLoop goroutine (and its HandleTrackEnded cleanup,
	// e.g. flushing the record-service forwarder) runs independently and
	// isn't guaranteed to have finished by the time pc.Close() returns --
	// see trackWG's doc. Waiting here is what makes it safe for the caller
	// (cmd/agent/main.go) to tear down the shared recordclient connection
	// right after Close() returns.
	trackWGDone := make(chan struct{})
	go func() {
		a.trackWG.Wait()
		close(trackWGDone)
	}()
	select {
	case <-trackWGDone:
	case <-time.After(trackCloseGrace):
		logging.L.Warn("rtcagent: track cleanup did not finish within timeout, continuing shutdown", "timeout", trackCloseGrace)
	}

	return closeErr
}

func (a *PeerAgent) handleTrack(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
	if a.closing.Load() {
		// See closing's doc -- Close() is already underway, refuse rather
		// than risk racing trackWG.Add against trackWG.Wait.
		logging.L.Warn("rtcagent: OnTrack fired after shutdown began, ignoring", "stream_id", track.StreamID())
		return
	}

	mid := a.midFor(receiver)
	if mid == "" {
		logging.L.Warn("rtcagent: OnTrack fired but no matching transceiver mid found", "stream_id", track.StreamID())
		return
	}

	midInt, err := strconv.Atoi(mid)
	if err != nil {
		logging.L.Warn("rtcagent: non-numeric mid, dropping track", "mid", mid)
		return
	}
	if midInt < remoteMidBase {
		// Our own uplink (0/1/2) shouldn't fire OnTrack for an audience
		// session (inactive), but guard anyway.
		logging.L.Warn("rtcagent: OnTrack fired for own uplink mid, ignoring", "mid", mid)
		return
	}

	kind, validMid := a.trackKindForMid(midInt)
	if !validMid {
		logging.L.Warn("rtcagent: mid outside negotiated media-pool layout, dropping track", "mid", mid)
		return
	}

	a.mu.Lock()
	// A fixed-pool slot_assigned can arrive before this track's one-time
	// OnTrack does (the slot is pre-allocated and the assignment is a plain
	// signaling message, typically faster than the first RTP packet
	// actually reaching us) -- see ApplySlotAssigned's doc. If that already
	// happened, it's the live, authoritative mapping and MUST be checked
	// before even attempting parseMsid: mezon-sfu's fixed-pool SDP (PR #475,
	// append_fixed_slot_audio/video in sdp.c) sets every slot's msid to the
	// pool-slot id ("a=msid:slot-<n> ..."), never a user/peer id, so
	// parseMsid is guaranteed to fail there -- checking it first (as this
	// code used to) made handleTrack drop every single fixed-pool track
	// unconditionally, regardless of slot_assigned having already arrived.
	info, known := a.midTable[mid]
	if !known {
		if userID, peerID, ok := parseMsid(track.StreamID()); ok && !a.mediaPool.IsDecoupled() {
			info = TrackInfo{Mid: mid, UserID: userID, PeerID: peerID, Kind: kind}
		} else {
			// Neither a slot_assigned seed nor a parseable msid yet -- either
			// a genuinely malformed msid, or the rare race where OnTrack beat
			// slot_assigned's signaling message. Seed a bare placeholder
			// instead of dropping the track outright: readLoop/currentInfo
			// re-read the table on every packet, so once ApplySlotAssigned
			// lands this self-heals, and bridge.sessionFor already finalizes
			// and recreates the session on the resulting PeerID mismatch.
			// Dropping here would silently lose this mid's audio for the
			// track's entire lifetime, since OnTrack only ever fires once.
			logging.L.Warn("rtcagent: unparseable msid and no slot_assigned seed yet, starting track with placeholder identity", "stream_id", track.StreamID(), "mid", mid)
			info = TrackInfo{Mid: mid, Kind: kind}
		}
		a.midTable[mid] = info
	}
	a.mu.Unlock()

	logging.L.Info("rtcagent: track received", "mid", mid, "user_id", info.UserID, "peer_id", info.PeerID, "kind", kind,
		"from_slot_assigned", known)

	a.trackWG.Add(1)
	go a.readLoop(track, mid, kind)
}

// currentInfo returns the live mid -> identity mapping (see
// ApplySlotAssigned's doc for why this can change after handleTrack first
// populated it). ok is false only if mid was somehow never seeded at all,
// which shouldn't happen in practice since handleTrack always sets it before
// starting readLoop.
func (a *PeerAgent) currentInfo(mid string) (TrackInfo, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	info, ok := a.midTable[mid]
	return info, ok
}

func (a *PeerAgent) currentAudioInfo(mid string) (TrackInfo, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	info, ok := a.midTable[mid]
	if !ok {
		return TrackInfo{}, false
	}
	if a.mediaPool.IsDecoupled() && (info.PeerID == 0 || !a.activeMids[mid]) {
		return TrackInfo{}, false
	}
	return info, true
}

// midFor finds the transceiver mid associated with an RTPReceiver. pion
// doesn't expose this directly on the receiver, so we scan transceivers.
func (a *PeerAgent) midFor(receiver *webrtc.RTPReceiver) string {
	for _, t := range a.pc.GetTransceivers() {
		if t.Receiver() == receiver {
			return t.Mid()
		}
	}
	return ""
}

func kindForMid(mid int) TrackKind {
	switch (mid - remoteMidBase) % 3 {
	case 0:
		return KindAudio
	case 1:
		return KindCamera
	default:
		return KindScreen
	}
}

func (a *PeerAgent) trackKindForMid(mid int) (TrackKind, bool) {
	if !a.mediaPool.IsDecoupled() {
		return kindForMid(mid), true
	}
	rest := mid - remoteMidBase
	if rest < 0 {
		return "", false
	}
	for _, block := range []struct {
		kind  TrackKind
		count uint32
	}{
		{KindAudio, a.mediaPool.MaxAudioSlots},
		{KindCamera, a.mediaPool.MaxCameraSlots},
		{KindScreen, a.mediaPool.MaxScreenSlots},
	} {
		if rest < int(block.count) {
			return block.kind, true
		}
		rest -= int(block.count)
	}
	return "", false
}

func parseMsid(streamID string) (userID int64, peerID uint64, ok bool) {
	m := msidRe.FindStringSubmatch(streamID)
	if m == nil {
		return 0, 0, false
	}
	uid, err := strconv.ParseInt(m[1], 10, 64)
	if err != nil {
		return 0, 0, false
	}
	pid, err := strconv.ParseUint(m[2], 10, 64)
	if err != nil {
		return 0, 0, false
	}
	return uid, pid, true
}

// readLoop drains RTP packets for one track so pion's internal buffers don't
// back up, and forwards mic-slot packets to OnAudioPacket if set. See the
// OnAudioPacket/OnTrackEnded doc for why both are called only from here.
//
// info is deliberately NOT a parameter captured once at track start: for a
// fixed-pool slot (see ApplySlotAssigned's doc) the mid can outlive several
// different owners without this read loop or its underlying track ever
// restarting, so every packet re-resolves the current owner via
// currentInfo. For a mid that's never touched by a slot event (the common,
// non-fixed-pool case) that's always the same value handleTrack seeded --
// this costs one mutex lock per packet for no behavior change there.
func (a *PeerAgent) readLoop(track *webrtc.TrackRemote, mid string, kind TrackKind) {
	defer a.trackWG.Done()
	var packetCount uint64
	for {
		pkt, _, err := track.ReadRTP()
		if err != nil {
			info, _ := a.currentInfo(mid)
			logging.L.Info("rtcagent: track ended", append(logging.ErrAttrs(err),
				"mid", mid, "user_id", info.UserID, "kind", kind, "packets_received", packetCount)...)
			break
		}
		packetCount++

		if kind == KindAudio && a.OnAudioPacket != nil {
			if info, ok := a.currentAudioInfo(mid); ok {
				a.OnAudioPacket(info, pkt)
			}
		}
	}

	if kind == KindAudio && a.OnTrackEnded != nil {
		a.OnTrackEnded(a.finalInfo(mid, kind))
	}
}

// finalInfo is currentInfo with a fallback for readLoop's end-of-track call:
// if the mid has no current owner anymore (e.g. the track's underlying
// transceiver closed right after this mid's slot was released and never
// reassigned -- ApplySlotReleased already cleared it), still return a
// TrackInfo carrying the real mid rather than the zero value.
// audiopipeline.Bridge keys its sessions purely by Mid, so passing an empty
// Mid to OnTrackEnded would make HandleTrackEnded silently miss the
// session, leaving its record-service stream never closed.
func (a *PeerAgent) finalInfo(mid string, kind TrackKind) TrackInfo {
	if info, ok := a.currentInfo(mid); ok {
		return info
	}
	return TrackInfo{Mid: mid, Kind: kind}
}
