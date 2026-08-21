// Package rtcagent wraps a pion PeerConnection configured to talk to
// mezon-sfu, and maintains the runtime `mid -> {user_id, is_screen}` table
// used to label incoming audio for the record-forwarding path.
//
// Recipe (mezon-sfu-migration-checklist.md D2, 2026-08-16 layout):
//   - Each peer has 3 fixed uplink slots: mid 0/1/2 = audio/camera/screen of
//     THIS agent; remote peers start at mid 3, 3-wide per peer
//     (SFU_REMOTE_MID_BASE=3): mid 3/4/5 = slot #1 audio/camera/screen,
//     mid 6/7/8 = slot #2, etc.
//   - `a=msid:u<user_id>-p<peer_id>` on every section: pion surfaces this as
//     TrackRemote.StreamID(), so user_id/peer_id are read directly off the
//     track -- no need to cross-reference room_snapshot/peer_joined for that
//     (we still keep a roster map for role/is_mute).
//   - mid position (offset 0/1/2 from the peer's 3-wide slot) tells us
//     mic vs camera vs screen without any heuristic.
package rtcagent

import (
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
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
	Mid    string
	UserID int64
	PeerID uint64
	Kind   TrackKind
}

var msidRe = regexp.MustCompile(`^u(-?\d+)-p(\d+)$`)

// PeerAgent owns one pion PeerConnection for the lifetime of one mezon-sfu
// session (one WS connection worth of ICE/DTLS/SRTP state).
type PeerAgent struct {
	pc *webrtc.PeerConnection

	mu       sync.Mutex
	midTable map[string]TrackInfo       // mid -> track info, from OnTrack
	roster   map[int64]signaling.Member // user_id -> latest roster entry (role/is_mute)

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
func New(iceServers []signaling.ICEServer, publishTrack webrtc.TrackLocal) (*PeerAgent, error) {
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
		pc:       pc,
		midTable: make(map[string]TrackInfo),
		roster:   make(map[int64]signaling.Member),
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

// UpsertRoster records/updates a roster entry (role/is_mute) for a user_id.
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
		}
	}
}

// Close tears down the PeerConnection. Closing the parent WS connection is
// what actually ends the mezon-sfu session server-side; this just releases
// local resources. Bounded by closeTimeout rather than trusting pion to
// always return promptly.
func (a *PeerAgent) Close() error {
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

	userID, peerID, ok := parseMsid(track.StreamID())
	if !ok {
		logging.L.Warn("rtcagent: unparseable msid, dropping track", "stream_id", track.StreamID(), "mid", mid)
		return
	}

	kind := kindForMid(midInt)
	info := TrackInfo{Mid: mid, UserID: userID, PeerID: peerID, Kind: kind}

	a.mu.Lock()
	a.midTable[mid] = info
	a.mu.Unlock()

	logging.L.Info("rtcagent: track received", "mid", mid, "user_id", userID, "peer_id", peerID, "kind", kind)

	a.trackWG.Add(1)
	go a.readLoop(track, info)
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
func (a *PeerAgent) readLoop(track *webrtc.TrackRemote, info TrackInfo) {
	defer a.trackWG.Done()
	var packetCount uint64
	for {
		pkt, _, err := track.ReadRTP()
		if err != nil {
			logging.L.Info("rtcagent: track ended", append(logging.ErrAttrs(err),
				"mid", info.Mid, "user_id", info.UserID, "kind", info.Kind, "packets_received", packetCount)...)
			break
		}
		packetCount++

		if info.Kind == KindAudio && a.OnAudioPacket != nil {
			a.OnAudioPacket(info, pkt)
		}
	}

	if info.Kind == KindAudio && a.OnTrackEnded != nil {
		a.OnTrackEnded(info)
	}
}
