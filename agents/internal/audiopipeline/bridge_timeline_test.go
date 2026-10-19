package audiopipeline

import (
	"sync"
	"testing"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

// fakeSink is a minimal Sink that just counts bytes, for asserting how much
// (and when) silence padding vs. real PCM gets forwarded.
type fakeSink struct {
	mu     sync.Mutex
	bytes  int
	closed bool
}

func (f *fakeSink) SendPCM(pcm []byte) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.bytes += len(pcm)
}

func (f *fakeSink) Close() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closed = true
}

func (f *fakeSink) snapshot() (bytes int, closed bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.bytes, f.closed
}

// silenceBytes is how many bytes of digital silence represent d at the
// pipeline's fixed sample rate/width (16kHz mono, 16-bit).
func silenceBytes(d time.Duration) int {
	samples := int(d.Nanoseconds() * int64(PCMSampleRate) / int64(time.Second))
	return samples * PCMChannels * 2
}

func rtpTimestampAfter(start uint32, d time.Duration) uint32 {
	return start + uint32(d.Nanoseconds()*int64(opusRTPClockRate)/int64(time.Second))
}

func TestRecordTimeline_PadsSilenceDuringRTPInactivity(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	realPCM := make([]byte, 320) // 10ms of 16kHz mono PCM16
	r.sendAllPcm(realPCM, 100000, t0)

	r.onTick(t0.Add(2 * time.Second))
	r.onTick(t0.Add(4 * time.Second))

	got, _ := sink.snapshot()
	want := len(realPCM) + silenceBytes(4*time.Second-rtpActivityGrace)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (real PCM plus inactivity padding)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_ResumeFillsGapOnlyToPacketStart(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	initialPCM := make([]byte, 320) // 10ms
	const firstTimestamp = uint32(100000)
	r.sendAllPcm(initialPCM, firstTimestamp, t0)
	r.onTick(t0.Add(2 * time.Second))

	resumedPCM := make([]byte, 320) // represents the 10ms immediately before arrival
	r.sendAllPcm(resumedPCM, rtpTimestampAfter(firstTimestamp, 2100*time.Millisecond), t0.Add(2100*time.Millisecond))

	// This tick is only 50ms after the resumed packet, inside the 70ms grace.
	r.onTick(t0.Add(2150 * time.Millisecond))

	got, _ := sink.snapshot()
	want := len(initialPCM) + silenceBytes(2090*time.Millisecond) + len(resumedPCM)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (padding must stop at resumed packet start)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_ResumeBeforeTickerBackfillsShortGap(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	initialPCM := make([]byte, 320) // 10ms
	resumedPCM := make([]byte, 320) // 10ms
	const firstTimestamp = uint32(200000)
	r.sendAllPcm(initialPCM, firstTimestamp, t0)
	r.sendAllPcm(resumedPCM, rtpTimestampAfter(firstTimestamp, 100*time.Millisecond), t0.Add(100*time.Millisecond))

	got, _ := sink.snapshot()
	want := len(initialPCM) + silenceBytes(90*time.Millisecond) + len(resumedPCM)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (90ms gap plus two real packets)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_DoesNotPadInsideGrace(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	realPCM := make([]byte, 320)
	r.sendAllPcm(realPCM, 300000, t0)
	r.onTick(t0.Add(rtpActivityGrace - time.Millisecond))

	got, _ := sink.snapshot()
	if got != len(realPCM) {
		t.Errorf("forwarded bytes = %d, want %d (no padding inside grace)", got, len(realPCM))
	}

	r.close(t0)
}

func TestRecordTimeline_FlushesInactivityRemainderOnClose(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	realPCM := make([]byte, 320)
	r.sendAllPcm(realPCM, 400000, t0)
	r.close(t0.Add(750 * time.Millisecond))

	got, closed := sink.snapshot()
	if !closed {
		t.Error("sink should be closed")
	}
	want := len(realPCM) + silenceBytes(750*time.Millisecond)
	if got != want {
		t.Errorf("forwarded bytes on close = %d, want %d", got, want)
	}
}

func TestRecordTimeline_RTPGapIgnoresArrivalDelay(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	packet := make([]byte, 640) // 20ms
	const firstTimestamp = uint32(500000)
	r.sendAllPcm(packet, firstTimestamp, t0)

	// The receiver sees a two-second arrival delay, but the RTP media clock
	// says the second packet starts only 500ms after the first packet began.
	r.sendAllPcm(packet, rtpTimestampAfter(firstTimestamp, 500*time.Millisecond), t0.Add(2*time.Second))

	got, _ := sink.snapshot()
	want := len(packet) + silenceBytes(480*time.Millisecond) + len(packet)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (padding must follow RTP timestamps, not arrival delay)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_BurstArrivalDoesNotPadBetweenConsecutiveRTPPackets(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	packet := make([]byte, 640) // 20ms
	const firstTimestamp = uint32(600000)
	for i := 0; i < 5; i++ {
		r.sendAllPcm(
			packet,
			firstTimestamp+uint32(i*960),
			t0.Add(time.Duration(i)*time.Millisecond),
		)
	}

	got, _ := sink.snapshot()
	want := 5 * len(packet)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (consecutive RTP packets need no silence even when arrivals are bursty)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_RTPGapHandlesTimestampWraparound(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	packet := make([]byte, 640) // 20ms / 960 RTP ticks
	firstTimestamp := ^uint32(0) - 479
	r.sendAllPcm(packet, firstTimestamp, t0)

	// The next consecutive timestamp wraps from the end of uint32 to 480.
	r.sendAllPcm(packet, firstTimestamp+960, t0.Add(20*time.Millisecond))

	got, _ := sink.snapshot()
	want := 2 * len(packet)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (timestamp wrap must not create silence)", got, want)
	}

	r.close(t0)
}

func TestRecordTimeline_LateRTPPacketDoesNotMoveTimelineBackward(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	packet := make([]byte, 640) // 20ms / 960 RTP ticks
	const firstTimestamp = uint32(700000)
	r.sendAllPcm(packet, firstTimestamp, t0)
	r.sendAllPcm(packet, firstTimestamp+960, t0.Add(20*time.Millisecond))

	// This duplicate/late packet starts before the already-recorded media
	// end and must neither be appended nor move the RTP cursor backward.
	r.sendAllPcm(packet, firstTimestamp, t0.Add(21*time.Millisecond))
	r.sendAllPcm(packet, firstTimestamp+1920, t0.Add(40*time.Millisecond))

	got, _ := sink.snapshot()
	want := 3 * len(packet)
	if got != want {
		t.Errorf("forwarded bytes = %d, want %d (late RTP must not extend or rewind the timeline)", got, want)
	}

	r.close(t0)
}

func TestBridge_HandleTrackEndedClosesRecordSink(t *testing.T) {
	sink := &fakeSink{}
	info := rtcagent.TrackInfo{Mid: "0", UserID: 1, PeerID: 42, Kind: rtcagent.KindAudio}
	b := NewBridge(func(rtcagent.TrackInfo) Sink { return sink }, nil)
	b.sessionFor(info)
	b.HandleTrackEnded(info)

	_, closed := sink.snapshot()
	if !closed {
		t.Error("sink should be closed after HandleTrackEnded")
	}
}

// TestBridge_SessionForReassignsOnPeerIDChange covers mezon-sfu PR #475's
// "fixed pool" mode: the same mid can go on streaming RTP for a different
// peer without the track itself ending (no renegotiation, no
// HandleTrackEnded -- see rtcagent.PeerAgent.ApplySlotAssigned's doc).
// sessionFor must notice the PeerID on an already-live session no longer
// matches, finalize the outgoing peer's sink, and start a fresh one for the
// new occupant -- otherwise both peers' audio would land in the same
// record-service track (the same room_id:track_id collision class of bug
// PR #278/#291 fixed for reconnects).
func TestBridge_SessionForReassignsOnPeerIDChange(t *testing.T) {
	var mu sync.Mutex
	sinksByPeer := map[uint64]*fakeSink{}
	b := NewBridge(func(info rtcagent.TrackInfo) Sink {
		s := &fakeSink{}
		mu.Lock()
		sinksByPeer[info.PeerID] = s
		mu.Unlock()
		return s
	}, nil)

	oldPeer := rtcagent.TrackInfo{Mid: "9", UserID: 111, PeerID: 10, Kind: rtcagent.KindAudio}
	newPeer := rtcagent.TrackInfo{Mid: "9", UserID: 222, PeerID: 20, Kind: rtcagent.KindAudio}

	s1 := b.sessionFor(oldPeer)
	if s1 == nil {
		t.Fatal("sessionFor(oldPeer) returned nil")
	}

	s2 := b.sessionFor(newPeer)
	if s2 == nil {
		t.Fatal("sessionFor(newPeer) returned nil")
	}
	if s2 == s1 {
		t.Fatal("sessionFor(newPeer) returned the old peer's session instead of a fresh one")
	}
	if s2.info.PeerID != 20 || s2.info.UserID != 222 {
		t.Errorf("new session info = %+v, want peer_id=20 user_id=222", s2.info)
	}

	mu.Lock()
	oldSink, newSink := sinksByPeer[10], sinksByPeer[20]
	mu.Unlock()
	if _, closed := oldSink.snapshot(); !closed {
		t.Error("old peer's sink should be closed once the mid was reassigned")
	}
	if _, closed := newSink.snapshot(); closed {
		t.Error("new peer's sink should still be open")
	}

	// A packet for the same (mid, peer) the session was just created for
	// must keep reusing it, not reassign again.
	if again := b.sessionFor(newPeer); again != s2 {
		t.Error("sessionFor(newPeer) a second time created yet another session")
	}
}

func TestBridge_StaleTrackEndedDoesNotCloseNewBindingRevision(t *testing.T) {
	b := NewBridge(nil, nil)
	oldInfo := rtcagent.TrackInfo{Mid: "3", UserID: 111, PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 1}
	newInfo := rtcagent.TrackInfo{Mid: "3", UserID: 111, PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 2}

	oldSession := b.sessionFor(oldInfo)
	newSession := b.sessionFor(newInfo)
	if oldSession == newSession {
		t.Fatal("new binding revision reused the old audio session")
	}
	b.HandleTrackEnded(oldInfo)

	b.mu.Lock()
	got := b.sessions["3"]
	b.mu.Unlock()
	if got != newSession {
		t.Fatal("stale end notification removed the new binding's session")
	}
}
