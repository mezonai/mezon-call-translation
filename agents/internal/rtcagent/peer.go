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
	"regexp"
	"strconv"
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

// New creates the PeerConnection using the ICE servers mezon-sfu returned in
// `joined`. mezon-sfu sends `a=ice-lite` (still true as of the 2026-08-16
// protocol, mezon-sfu/src/protocol/signaling/sdp.c:370) -- pion is a full
// ICE agent by default, which is exactly what's required here (no special
// SettingEngine config needed for that).
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

	pc, err := webrtc.NewPeerConnection(cfg)
	if err != nil {
		return nil, fmt.Errorf("rtcagent: new peer connection: %w", err)
	}

	a := &PeerAgent{
		pc:       pc,
		midTable: make(map[string]TrackInfo),
		roster:   make(map[int64]signaling.Member),
	}

	if publishTrack != nil {
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

func convertICEServers(servers []signaling.ICEServer) []webrtc.ICEServer {
	out := make([]webrtc.ICEServer, 0, len(servers))
	for _, s := range servers {
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
	select {
	case err := <-done:
		return err
	case <-time.After(closeTimeout):
		logging.L.Warn("rtcagent: PeerConnection.Close did not return within timeout, continuing shutdown", "timeout", closeTimeout)
		return nil
	}
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
