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

// Reproduces PR #291's fix: a peer that mutes mid-track should have silence
// padded into the record sink instead of just going silent (the original bug
// -- record-service appends whatever PCM it receives assuming a constant
// sample rate, so a gap here shows up as lost wall-clock time once
// summary_service reconstructs absolute segment timestamps from it).
func TestRecordTimeline_PadsSilenceWhileMuted(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)
	defer r.close(time.Now())

	t0 := time.Now()
	r.setMuted(true, t0)

	// Simulate two silence ticks (recordSilenceTick is 2s in production;
	// onTick is called directly here instead of waiting on the real ticker).
	r.onTick(t0.Add(2 * time.Second))
	r.onTick(t0.Add(4 * time.Second))

	got, _ := sink.snapshot()
	want := silenceBytes(4 * time.Second)
	if got != want {
		t.Errorf("padded bytes = %d, want %d (4s of silence)", got, want)
	}
}

// Real RTP must always win over synthetic silence: if a packet arrives while
// the timeline still thinks the peer is muted (e.g. the signaling
// peer_updated hasn't caught up yet), it must be forwarded as-is and must not
// be immediately followed by a redundant silence chunk covering the same
// span.
func TestRecordTimeline_RealAudioWinsOverSilence(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)
	defer r.close(time.Now())

	t0 := time.Now()
	r.setMuted(true, t0)
	r.onTick(t0.Add(2 * time.Second))

	afterFirstTick, _ := sink.snapshot()
	if afterFirstTick != silenceBytes(2*time.Second) {
		t.Fatalf("setup: expected 2s of silence before real packet, got %d bytes", afterFirstTick)
	}

	realPCM := make([]byte, 320) // 10ms of 16kHz mono 16-bit audio
	r.sendRealPCM(realPCM, t0.Add(2100*time.Millisecond))

	// A tick immediately after the real packet must not pad -- lastRTPAt is
	// inside rtpActivityGrace of now.
	r.onTick(t0.Add(2150 * time.Millisecond))

	got, _ := sink.snapshot()
	want := afterFirstTick + len(realPCM)
	if got != want {
		t.Errorf("bytes after real packet = %d, want %d (no extra padding should have been inserted)", got, want)
	}
}

// Unmuting must flush whatever silence has accrued since the last pad, so a
// mute shorter than one silence tick isn't silently dropped.
func TestRecordTimeline_FlushesRemainderOnUnmute(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)
	defer r.close(time.Now())

	t0 := time.Now()
	r.setMuted(true, t0)
	r.setMuted(false, t0.Add(500*time.Millisecond))

	got, _ := sink.snapshot()
	want := silenceBytes(500 * time.Millisecond)
	if got != want {
		t.Errorf("padded bytes on unmute = %d, want %d (500ms)", got, want)
	}
}

// Closing a track mid-mute must flush the final remainder too, not drop it.
func TestRecordTimeline_FlushesRemainderOnClose(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)

	t0 := time.Now()
	r.setMuted(true, t0)
	r.close(t0.Add(750 * time.Millisecond))

	bytes, closed := sink.snapshot()
	if !closed {
		t.Error("sink should be closed")
	}
	want := silenceBytes(750 * time.Millisecond)
	if bytes != want {
		t.Errorf("padded bytes on close = %d, want %d (750ms)", bytes, want)
	}
}

// A newly-started track (e.g. renegotiation mid-call) must pick up the
// peer's current mute state immediately, not default to unmuted and miss the
// padding for however long it takes the next peer_updated to arrive.
func TestBridge_NewTrackPicksUpCachedMuteState(t *testing.T) {
	sink := &fakeSink{}
	b := NewBridge(func(rtcagent.TrackInfo) Sink { return sink }, nil)

	b.SetPeerMuted(7, true)

	info := rtcagent.TrackInfo{Mid: "0", UserID: 1, PeerID: 7, Kind: rtcagent.KindAudio}
	s := b.sessionFor(info)
	if s == nil || s.record == nil {
		t.Fatal("expected a record session to be created")
	}

	t0 := time.Now()
	s.record.onTick(t0.Add(2 * time.Second))

	got, _ := sink.snapshot()
	if got == 0 {
		t.Error("new track should have started muted (padding silence), got 0 bytes forwarded")
	}
}

// SetPeerMuted and HandleTrackEnded can legitimately race (a mute toggle
// arriving right as the peer leaves). Run with -race: this must not panic,
// deadlock, or double-close the sink.
func TestBridge_ConcurrentMuteAndTrackEnd(t *testing.T) {
	sink := &fakeSink{}
	info := rtcagent.TrackInfo{Mid: "0", UserID: 1, PeerID: 42, Kind: rtcagent.KindAudio}
	b := NewBridge(func(rtcagent.TrackInfo) Sink { return sink }, nil)
	b.sessionFor(info)

	var wg sync.WaitGroup
	stop := make(chan struct{})

	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
				b.SetPeerMuted(42, true)
				b.SetPeerMuted(42, false)
			}
		}
	}()

	time.Sleep(20 * time.Millisecond)
	b.HandleTrackEnded(info)
	close(stop)
	wg.Wait()

	// A mute toggle arriving after the track ended must be a harmless no-op.
	b.SetPeerMuted(42, true)

	_, closed := sink.snapshot()
	if !closed {
		t.Error("sink should be closed after HandleTrackEnded")
	}
}
