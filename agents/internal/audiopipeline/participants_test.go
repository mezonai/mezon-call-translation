package audiopipeline

import (
	"sync"
	"testing"
	"time"

	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

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
	return &rtp.Packet{Header: rtp.Header{SSRC: testSSRC, Timestamp: ts}, Payload: payload}
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

func TestDecoupledBridge_SlotMoveKeepsOneRecording(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink)
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
	_, closed := rec.sink(t, 0).snapshot()
	if closed {
		t.Fatal("slot release closed the participant's recording")
	}
	if duration := chainedOggDuration(t, rec.sink(t, 0).oggData()); duration < 350*time.Millisecond || duration > 800*time.Millisecond {
		t.Fatalf("recording duration across slot move = %s, want roughly 500ms", duration)
	}
	if rec.infos[0].PeerID != 10 || rec.infos[0].Kind != rtcagent.KindAudio {
		t.Fatalf("sink created with %+v", rec.infos[0])
	}
}

func TestDecoupledBridge_SeparateRecordingPerPeer(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink)
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
	b := NewDecoupledBridge(rec.newSink)
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
	b := NewDecoupledBridge(rec.newSink)
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
	b := NewDecoupledBridge(rec.newSink)
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
	if got := chainedOggDuration(t, rec.sink(t, 0).oggData()); got != 40*time.Millisecond {
		t.Fatalf("duration = %s, want both 20ms live frames", got)
	}
}

func chainedOggDuration(t *testing.T, data []byte) time.Duration {
	t.Helper()
	maxBySerial := map[uint32]uint64{}
	for _, page := range parseOggPages(t, data) {
		if page.granule != ^uint64(0) && page.granule > maxBySerial[page.serial] {
			maxBySerial[page.serial] = page.granule
		}
	}
	var ticks uint64
	for _, granule := range maxBySerial {
		ticks += granule
	}
	return time.Duration(ticks) * time.Second / RecordOpusSampleRate
}

func TestDecoupledBridge_PeerEndedClosesAndBlocksLatePackets(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink)
	defer b.Close()
	p := newOpusPackets(t)

	info := audioBinding("3", 10, 1)
	p.sendFrames(b, info, 1000, 1)
	p.sendFrames(b, audioBinding("4", 20, 2), 1000, 1)
	b.HandlePeerEnded(10)

	if _, closed := rec.sink(t, 0).snapshot(); !closed {
		t.Fatal("peer_left did not close the record sink")
	}
	if _, closed := rec.sink(t, 1).snapshot(); closed {
		t.Fatal("peer_left closed another peer's recording")
	}

	p.sendFrames(b, audioBinding("5", 10, 3), 1000, 1)
	if n := rec.count(); n != 2 {
		t.Fatalf("late packet after peer_left recreated a recording (%d sinks)", n)
	}
}

func TestDecoupledBridge_CloseClosesAllAndRejectsNewPackets(t *testing.T) {
	rec := &sinkRecorder{}
	b := NewDecoupledBridge(rec.newSink)
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
	b := NewDecoupledBridge(rec.newSink)
	defer b.Close()

	var wg sync.WaitGroup
	for i, info := range []rtcagent.TrackInfo{audioBinding("3", 10, 1), audioBinding("4", 10, 2)} {
		p := newOpusPackets(t) // one encoder per goroutine, built on the test goroutine
		base := uint32(i) * 1_000_000
		wg.Go(func() { p.sendFrames(b, info, base, 20) })
	}
	wg.Go(func() {
		b.HandleTrackEnded(audioBinding("3", 10, 1))
	})
	wg.Wait()

	if n := rec.count(); n != 1 {
		t.Fatalf("record sinks = %d, want 1", n)
	}
}
