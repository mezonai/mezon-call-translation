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

func TestRecordTimeline_PadsSilenceDuringRTPInactivity(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	realPCM := make([]byte, 320) // 10ms of 16kHz mono PCM16
	r.sendAllPcm(realPCM, t0)

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
	r.sendAllPcm(initialPCM, t0)
	r.onTick(t0.Add(2 * time.Second))

	resumedPCM := make([]byte, 320) // represents the 10ms immediately before arrival
	r.sendAllPcm(resumedPCM, t0.Add(2100*time.Millisecond))

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
	r.sendAllPcm(initialPCM, t0)
	r.sendAllPcm(resumedPCM, t0.Add(100*time.Millisecond))

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
	r.sendAllPcm(realPCM, t0)
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
	r.sendAllPcm(realPCM, t0)
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
