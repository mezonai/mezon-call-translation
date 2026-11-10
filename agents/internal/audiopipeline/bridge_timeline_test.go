package audiopipeline

import (
	"encoding/binary"
	"sync"
	"testing"
	"time"

	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

const testSSRC = 42

type fakeSink struct {
	mu         sync.Mutex
	data       []byte
	closed     bool
	closeCount int
}

func (f *fakeSink) SendOgg(ogg []byte) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data = append(f.data, ogg...)
}

func (f *fakeSink) Close() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closed = true
	f.closeCount++
}

func (f *fakeSink) snapshot() (int, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.data), f.closed
}

func (f *fakeSink) oggData() []byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]byte(nil), f.data...)
}

type oggPageInfo struct {
	headerType byte
	serial     uint32
	granule    uint64
}

func parseOggPages(t *testing.T, data []byte) []oggPageInfo {
	t.Helper()
	var pages []oggPageInfo
	for offset := 0; offset < len(data); {
		if offset+27 > len(data) || string(data[offset:offset+4]) != "OggS" {
			t.Fatalf("invalid Ogg page at byte %d", offset)
		}
		segmentCount := int(data[offset+26])
		segmentEnd := offset + 27 + segmentCount
		if segmentEnd > len(data) {
			t.Fatalf("truncated segment table at byte %d", offset)
		}
		payloadLen := 0
		for _, size := range data[offset+27 : segmentEnd] {
			payloadLen += int(size)
		}
		pageEnd := segmentEnd + payloadLen
		if pageEnd > len(data) {
			t.Fatalf("truncated Ogg page at byte %d", offset)
		}
		pages = append(pages, oggPageInfo{
			headerType: data[offset+5],
			granule:    binary.LittleEndian.Uint64(data[offset+6:]),
			serial:     binary.LittleEndian.Uint32(data[offset+14:]),
		})
		offset = pageEnd
	}
	return pages
}

func oggDuration(t *testing.T, data []byte) time.Duration {
	t.Helper()
	var largest uint64
	for _, page := range parseOggPages(t, data) {
		if page.granule != ^uint64(0) && page.granule > largest {
			largest = page.granule
		}
	}
	return time.Duration(largest) * time.Second / RecordOpusSampleRate
}

func newTimelineTest(t *testing.T) (*recordTimeline, *fakeSink, opusenc.Encoder) {
	t.Helper()
	sink := &fakeSink{}
	timeline, err := newRecordTimeline(sink, testSSRC)
	if err != nil {
		t.Fatalf("newRecordTimeline: %v", err)
	}
	encoder, err := opusenc.New(RecordOpusSampleRate, RecordOpusChannels)
	if err != nil {
		t.Fatalf("opusenc.New: %v", err)
	}
	return timeline, sink, encoder
}

func sendOggTestPacket(
	t *testing.T,
	timeline *recordTimeline,
	encoder opusenc.Encoder,
	ssrc, timestamp uint32,
	ticks int,
	now time.Time,
	newBinding bool,
) *oggStreamTransition {
	t.Helper()
	payload, err := encoder.Encode(make([]int16, ticks*RecordOpusChannels))
	if err != nil {
		t.Fatalf("encode packet: %v", err)
	}
	transition, err := timeline.sendAllOgg(
		&rtp.Packet{Header: rtp.Header{SSRC: ssrc, Timestamp: timestamp}, Payload: payload},
		uint32(ticks), now, newBinding,
	)
	if err != nil {
		t.Fatalf("sendAllOgg: %v", err)
	}
	return transition
}

func TestRecordTimeline_PadsOggSilenceUsingRTPTime(t *testing.T) {
	r, sink, encoder := newTimelineTest(t)
	t0 := time.Now()
	const firstTimestamp = 100000
	sendOggTestPacket(t, r, encoder, testSSRC, firstTimestamp, 480, t0, false)
	if err := r.onTick(t0.Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	sendOggTestPacket(t, r, encoder, testSSRC, firstTimestamp+100800, 480, t0.Add(2100*time.Millisecond), false)

	if got, want := oggDuration(t, sink.oggData()), 2110*time.Millisecond; got != want {
		t.Fatalf("Ogg duration = %s, want %s", got, want)
	}
	r.close(t0.Add(2100 * time.Millisecond))
}

func TestRecordTimeline_LatePacketDoesNotRewindOgg(t *testing.T) {
	r, sink, encoder := newTimelineTest(t)
	t0 := time.Now()
	const ts = 700000
	sendOggTestPacket(t, r, encoder, testSSRC, ts, 960, t0, false)
	sendOggTestPacket(t, r, encoder, testSSRC, ts+960, 960, t0.Add(20*time.Millisecond), false)
	sendOggTestPacket(t, r, encoder, testSSRC, ts, 960, t0.Add(21*time.Millisecond), false)
	sendOggTestPacket(t, r, encoder, testSSRC, ts+1920, 960, t0.Add(40*time.Millisecond), false)
	if got := oggDuration(t, sink.oggData()); got != 60*time.Millisecond {
		t.Fatalf("Ogg duration = %s, want 60ms", got)
	}
	r.close(t0.Add(40 * time.Millisecond))
}

func TestRecordTimeline_ChainsOggStreamsOnSSRCRotation(t *testing.T) {
	r, sink, encoder := newTimelineTest(t)
	t0 := time.Now()
	sendOggTestPacket(t, r, encoder, testSSRC, 900000, 960, t0, false)
	const rotatedSSRC = 99
	transition := sendOggTestPacket(t, r, encoder, rotatedSSRC, 12345, 960, t0.Add(20*time.Millisecond), false)
	if transition == nil || transition.reason != "ssrc_rotation" || transition.oldSSRC != testSSRC || transition.newSSRC != rotatedSSRC {
		t.Fatalf("transition = %+v", transition)
	}
	r.close(t0.Add(20 * time.Millisecond))
	assertTwoCompleteChains(t, sink.oggData())
	if sink.closeCount != 1 {
		t.Fatalf("sink close count = %d, want 1", sink.closeCount)
	}
}

func TestRecordTimeline_ChainsOnBindingChangeWithReusedSSRC(t *testing.T) {
	r, sink, encoder := newTimelineTest(t)
	t0 := time.Now()
	sendOggTestPacket(t, r, encoder, testSSRC, 900000, 960, t0, false)
	transition := sendOggTestPacket(t, r, encoder, testSSRC, 5, 960, t0.Add(500*time.Millisecond), true)
	if transition == nil || transition.reason != "binding_change" {
		t.Fatalf("transition = %+v, want binding_change", transition)
	}
	r.close(t0.Add(500 * time.Millisecond))
	assertTwoCompleteChains(t, sink.oggData())
}

func assertTwoCompleteChains(t *testing.T, data []byte) {
	t.Helper()
	pages := parseOggPages(t, data)
	var bos, eos []oggPageInfo
	for _, page := range pages {
		if page.headerType&0x02 != 0 {
			bos = append(bos, page)
		}
		if page.headerType&0x04 != 0 {
			eos = append(eos, page)
		}
	}
	if len(bos) != 2 || len(eos) != 2 {
		t.Fatalf("BOS=%d EOS=%d, want two complete logical streams", len(bos), len(eos))
	}
	if bos[0].serial == bos[1].serial || eos[0].serial != bos[0].serial || eos[1].serial != bos[1].serial {
		t.Fatalf("invalid chained serials: BOS=%v EOS=%v", bos, eos)
	}
}

func TestBridge_HandleTrackEndedClosesRecordSink(t *testing.T) {
	sink := &fakeSink{}
	info := rtcagent.TrackInfo{Mid: "0", UserID: 1, PeerID: 42, Kind: rtcagent.KindAudio}
	b := NewBridge(func(rtcagent.TrackInfo) Sink { return sink })
	b.sessionFor(info, testSSRC)
	b.HandleTrackEnded(info)
	if _, closed := sink.snapshot(); !closed {
		t.Error("sink should be closed after HandleTrackEnded")
	}
}

func TestBridge_StaleTrackEndedDoesNotCloseNewBindingRevision(t *testing.T) {
	b := NewBridge(nil)
	oldInfo := rtcagent.TrackInfo{Mid: "3", UserID: 111, PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 1}
	newInfo := rtcagent.TrackInfo{Mid: "3", UserID: 111, PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 2}
	oldSession := b.sessionFor(oldInfo, testSSRC)
	newSession := b.sessionFor(newInfo, testSSRC)
	if oldSession == newSession {
		t.Fatal("new binding revision reused old session")
	}
	b.HandleTrackEnded(oldInfo)
	b.mu.Lock()
	got := b.sessions["3"]
	b.mu.Unlock()
	if got != newSession {
		t.Fatal("stale end notification removed new binding")
	}
}
