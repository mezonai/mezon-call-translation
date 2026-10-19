package audiopipeline

import (
	"sync"
	"testing"
	"time"

	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

// opusFrameBytes is the 16kHz mono PCM16 size of one decoded 20ms Opus frame.
var opusFrameBytes = silenceBytes(20 * time.Millisecond)

// sinkRecorder hands out a fresh fakeSink per factory call so tests can
// assert how many recordings/STT sessions a participant produced.
type sinkRecorder struct {
	mu    sync.Mutex
	sinks []*fakeSink
	infos []rtcagent.TrackInfo
}

func (r *sinkRecorder) newSink(info rtcagent.TrackInfo) Sink {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := &fakeSink{}
	r.sinks = append(r.sinks, s)
	r.infos = append(r.infos, info)
	return s
}

func (r *sinkRecorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.sinks)
}

func (r *sinkRecorder) sink(t *testing.T, i int) *fakeSink {
	t.Helper()
	r.mu.Lock()
	defer r.mu.Unlock()
	if i >= len(r.sinks) {
		t.Fatalf("sink %d requested, only %d created", i, len(r.sinks))
	}
	return r.sinks[i]
}

type opusPackets struct {
	t   *testing.T
	enc opusenc.Encoder
}

func newOpusPackets(t *testing.T) *opusPackets {
	t.Helper()
	enc, err := opusenc.New(48000, 1)
	if err != nil {
		t.Fatalf("opusenc.New: %v", err)
	}
	return &opusPackets{t: t, enc: enc}
}

// packet returns one 20ms Opus packet with the given RTP timestamp.
func (p *opusPackets) packet(ts uint32) *rtp.Packet {
	p.t.Helper()
	pcm := make([]int16, 960)
	for i := range pcm {
		pcm[i] = int16(i % 200)
	}
	payload, err := p.enc.Encode(pcm)
	if err != nil {
		p.t.Fatalf("Encode: %v", err)
	}
	return &rtp.Packet{Header: rtp.Header{Timestamp: ts}, Payload: payload}
}

// sendFrames feeds n consecutive 20ms packets for info starting at ts.
func (p *opusPackets) sendFrames(b *Bridge, info rtcagent.TrackInfo, ts uint32, n int) {
	for i := range n {
		b.HandlePacket(info, p.packet(ts+uint32(960*i)))
	}
}

func audioBinding(mid string, peerID uint64, revision uint64) rtcagent.TrackInfo {
	return rtcagent.TrackInfo{Mid: mid, UserID: int64(peerID) * 100, PeerID: peerID, Kind: rtcagent.KindAudio, BindingRevision: revision}
}

func TestRecordTimeline_NewBindingPadsArrivalGap(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)
	defer r.close(time.Now())

	t0 := time.Now()
	pcm := make([]byte, opusFrameBytes)
	r.sendPCM(pcm, 1000, t0, true)
	// The new binding's RTP clock is unrelated (and here even behind the old
	// one); only the 500ms arrival gap may decide the silence length.
	r.sendPCM(pcm, 5, t0.Add(500*time.Millisecond), true)

	got, _ := sink.snapshot()
	want := 2*len(pcm) + silenceBytes(500*time.Millisecond-20*time.Millisecond)
	if got != want {
		t.Fatalf("bytes = %d, want %d", got, want)
	}
}

func TestRecordTimeline_NewBindingSubtractsTickerPadding(t *testing.T) {
	sink := &fakeSink{}
	r := newRecordTimeline(sink)
	defer r.close(time.Now())

	t0 := time.Now()
	pcm := make([]byte, opusFrameBytes)
	r.sendPCM(pcm, 1000, t0, true)
	r.onTick(t0.Add(2 * time.Second)) // muted: ticker already committed some silence
	r.sendPCM(pcm, 77, t0.Add(3*time.Second), true)

	got, _ := sink.snapshot()
	want := 2*len(pcm) + silenceBytes(3*time.Second-20*time.Millisecond)
	if got != want {
		t.Fatalf("bytes = %d, want %d (silence double-counted or missing)", got, want)
	}
}

func TestDecoupledBridge_SlotMoveKeepsOneRecording(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()
	p := newOpusPackets(t)

	first := audioBinding("3", 10, 1)
	p.sendFrames(b, first, 1000, 5)
	b.HandleTrackEnded(first) // mute: slot_released
	time.Sleep(200 * time.Millisecond)
	second := audioBinding("4", 10, 2) // unmute onto another audio slot
	p.sendFrames(b, second, 9_000_000, 5)

	if n := rec.count(); n != 1 {
		t.Fatalf("record sinks = %d, want 1 per participant", n)
	}
	got, closed := rec.sink(t, 0).snapshot()
	if closed {
		t.Fatal("slot release closed the participant's recording")
	}
	silence := got - 10*opusFrameBytes
	if silence < silenceBytes(150*time.Millisecond) || silence > silenceBytes(500*time.Millisecond) {
		t.Fatalf("silence across the slot move = %d bytes, want roughly 180ms (%d)", silence, silenceBytes(180*time.Millisecond))
	}
	if rec.infos[0].PeerID != 10 || rec.infos[0].Kind != rtcagent.KindAudio {
		t.Fatalf("sink created with %+v", rec.infos[0])
	}
}

func TestDecoupledBridge_SeparateRecordingPerPeer(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()
	p := newOpusPackets(t)

	// Same MID handed from peer 10 to peer 20 (VAD swap).
	p.sendFrames(b, audioBinding("3", 10, 1), 1000, 2)
	p.sendFrames(b, audioBinding("3", 20, 2), 1000, 2)

	if n := rec.count(); n != 2 {
		t.Fatalf("record sinks = %d, want one per peer", n)
	}
	if rec.infos[0].PeerID != 10 || rec.infos[1].PeerID != 20 {
		t.Fatalf("sink owners = %d,%d want 10,20", rec.infos[0].PeerID, rec.infos[1].PeerID)
	}
}

func TestDecoupledBridge_DropsOlderBindingPackets(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()
	p := newOpusPackets(t)

	oldBinding := audioBinding("3", 10, 1)
	newBinding := audioBinding("4", 10, 2)
	p.sendFrames(b, oldBinding, 1000, 1)
	p.sendFrames(b, newBinding, 50_000, 1)
	before, _ := rec.sink(t, 0).snapshot()

	// The old MID's read loop may still deliver a queued packet.
	p.sendFrames(b, oldBinding, 1960, 1)
	after, _ := rec.sink(t, 0).snapshot()
	if after != before {
		t.Fatalf("stale binding wrote %d bytes into the participant recording", after-before)
	}
}

func TestDecoupledBridge_RetiredBindingCannotRecreateSession(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()
	p := newOpusPackets(t)

	retired := audioBinding("3", 10, 4)
	b.HandleTrackEnded(retired)
	p.sendFrames(b, retired, 1000, 1)
	p.sendFrames(b, audioBinding("3", 10, 3), 1000, 1)
	if n := rec.count(); n != 0 {
		t.Fatalf("retired/older binding created %d sinks", n)
	}

	p.sendFrames(b, audioBinding("3", 10, 5), 1000, 1)
	if n := rec.count(); n != 1 {
		t.Fatalf("newer binding on the retired MID created %d sinks, want 1", n)
	}
}

func TestDecoupledBridge_IgnoresPlaceholderIdentity(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()
	p := newOpusPackets(t)

	live := audioBinding("3", 10, 2)
	p.sendFrames(b, live, 1000, 1)
	// A read loop ending with no owner must not retire the live binding.
	b.HandleTrackEnded(rtcagent.TrackInfo{Mid: "3", Kind: rtcagent.KindAudio})
	p.sendFrames(b, rtcagent.TrackInfo{Mid: "3", Kind: rtcagent.KindAudio}, 1960, 1)
	p.sendFrames(b, live, 1960, 1)

	if n := rec.count(); n != 1 {
		t.Fatalf("record sinks = %d, want 1 (placeholder must not get a sink)", n)
	}
	if got, _ := rec.sink(t, 0).snapshot(); got != 2*opusFrameBytes {
		t.Fatalf("bytes = %d, want both live frames (%d)", got, 2*opusFrameBytes)
	}
}

func TestDecoupledBridge_PeerEndedClosesAndBlocksLatePackets(t *testing.T) {
	rec := &sinkRecorder{}
	stt := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, stt.newSink)
	defer b.Close()
	b.SetSTTEnabled(true)
	p := newOpusPackets(t)

	info := audioBinding("3", 10, 1)
	p.sendFrames(b, info, 1000, 1)
	p.sendFrames(b, audioBinding("4", 20, 2), 1000, 1)
	b.HandlePeerEnded(10)

	if _, closed := rec.sink(t, 0).snapshot(); !closed {
		t.Fatal("peer_left did not close the record sink")
	}
	if _, closed := stt.sink(t, 0).snapshot(); !closed {
		t.Fatal("peer_left did not close the STT sink")
	}
	if _, closed := rec.sink(t, 1).snapshot(); closed {
		t.Fatal("peer_left closed another peer's recording")
	}

	p.sendFrames(b, audioBinding("5", 10, 3), 1000, 1)
	if n := rec.count(); n != 2 {
		t.Fatalf("late packet after peer_left recreated a recording (%d sinks)", n)
	}
}

func TestDecoupledBridge_STTToggleKeepsRecording(t *testing.T) {
	rec := &sinkRecorder{}
	stt := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, stt.newSink)
	defer b.Close()
	p := newOpusPackets(t)

	info := audioBinding("3", 10, 1)
	p.sendFrames(b, info, 1000, 1)
	if n := stt.count(); n != 0 {
		t.Fatalf("STT sinks = %d while disabled", n)
	}

	b.SetSTTEnabled(true)
	if n := stt.count(); n != 1 {
		t.Fatalf("enabling STT created %d sinks for the live participant, want 1", n)
	}
	p.sendFrames(b, info, 1960, 1)
	if got, _ := stt.sink(t, 0).snapshot(); got != opusFrameBytes {
		t.Fatalf("STT bytes = %d, want one frame", got)
	}

	b.SetSTTEnabled(false)
	if _, closed := stt.sink(t, 0).snapshot(); !closed {
		t.Fatal("disabling STT left the STT sink open")
	}
	if n := rec.count(); n != 1 {
		t.Fatalf("STT toggle changed record sinks to %d", n)
	}
	if _, closed := rec.sink(t, 0).snapshot(); closed {
		t.Fatal("STT toggle closed the recording")
	}
}

func TestDecoupledBridge_CloseClosesAllAndRejectsNewPackets(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	p := newOpusPackets(t)

	p.sendFrames(b, audioBinding("3", 10, 1), 1000, 1)
	p.sendFrames(b, audioBinding("4", 20, 2), 1000, 1)
	b.Close()

	for i := range 2 {
		if _, closed := rec.sink(t, i).snapshot(); !closed {
			t.Fatalf("sink %d still open after Close", i)
		}
	}
	p.sendFrames(b, audioBinding("5", 30, 3), 1000, 1)
	if n := rec.count(); n != 2 {
		t.Fatalf("packet after Close created a sink (%d total)", n)
	}
}

// Run with -race: old and new MID read loops overlap during a slot move.
func TestDecoupledBridge_ConcurrentBindingsShareOneRecording(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink, nil)
	defer b.Close()

	var wg sync.WaitGroup
	for i, info := range []rtcagent.TrackInfo{audioBinding("3", 10, 1), audioBinding("4", 10, 2)} {
		p := newOpusPackets(t) // one encoder per goroutine, built on the test goroutine
		base := uint32(i) * 1_000_000
		wg.Go(func() { p.sendFrames(b, info, base, 20) })
	}
	wg.Go(func() {
		b.SetSTTEnabled(true)
		b.HandleTrackEnded(audioBinding("3", 10, 1))
	})
	wg.Wait()

	if n := rec.count(); n != 1 {
		t.Fatalf("record sinks = %d, want 1", n)
	}
}
