package audiopipeline

import (
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

// A zero-length SILK frame (TOC only), also used by pion/opus's decoder tests.
// It exercises actual decoding, rather than manually constructing sessions.
func silentPacket(timestamp uint32) *rtp.Packet {
	return &rtp.Packet{Header: rtp.Header{Timestamp: timestamp}, Payload: []byte{0}}
}

type countingSink struct {
	fakeSink
	closes atomic.Int32
}

func (s *countingSink) Close() {
	s.closes.Add(1)
	s.fakeSink.Close()
}

func TestDecoupledBridgeMuteResumeAndSlotReuse(t *testing.T) {
	var records, transcribers []*countingSink
	b := NewDecoupledBridge(func(rtcagent.TrackInfo) Sink {
		s := &countingSink{}
		records = append(records, s)
		return s
	}, func(rtcagent.TrackInfo) Sink {
		s := &countingSink{}
		transcribers = append(transcribers, s)
		return s
	})
	t.Cleanup(b.Close)
	b.SetSTTEnabled(true)
	a := rtcagent.TrackInfo{Mid: "3", PeerID: 10, UserID: 111, Kind: rtcagent.KindAudio, BindingRevision: 1}
	b.HandlePacket(a, silentPacket(100000))
	if len(records) != 1 || len(transcribers) != 1 {
		t.Fatal("first decoded packet must open one recording and STT sink")
	}
	s := b.participants.sessions[participantKey{a.PeerID, a.Kind}]
	firstDecoder := s.binding
	b.HandleTrackEnded(a) // mute releases audio, not the participant
	if records[0].closes.Load() != 0 || transcribers[0].closes.Load() != 0 {
		t.Fatal("mute closed participant sinks")
	}
	// A late old packet cannot resurrect its retired decoder.
	b.HandlePacket(a, silentPacket(100480))
	if s.binding != nil {
		t.Fatal("retired packet resurrected a binding")
	}
	// Another participant reuses A's old slot. Their sinks must be separate.
	other := rtcagent.TrackInfo{Mid: "3", PeerID: 20, UserID: 222, Kind: rtcagent.KindAudio, BindingRevision: 2}
	b.HandlePacket(other, silentPacket(400000))
	// A resumes on another MID with an unrelated RTP clock.
	resumed := a
	resumed.Mid, resumed.BindingRevision = "4", 3
	b.HandlePacket(resumed, silentPacket(100))
	if len(records) != 2 || len(transcribers) != 2 {
		t.Fatalf("slot move recreated participant sinks: record=%d stt=%d", len(records), len(transcribers))
	}
	if s.binding == nil || s.binding == firstDecoder || s.binding.info != resumed {
		t.Fatal("slot move must replace only the decoder binding")
	}
	if n, _ := transcribers[0].snapshot(); n != 2*silenceBytes(10*time.Millisecond) {
		t.Fatalf("A's STT received %d bytes, want only A's two decoded packets", n)
	}
	// A delayed/duplicate callback must not retire the resumed binding.
	b.HandleTrackEnded(a)
	if s.binding == nil || s.binding.info != resumed {
		t.Fatal("old end notification removed the new binding")
	}
	b.HandleTrackEnded(resumed)
	resumed.BindingRevision = 4 // resume on the same MID, new generation
	b.HandlePacket(resumed, silentPacket(200))
	if len(records) != 2 || records[0].closes.Load() != 0 {
		t.Fatal("same-MID unmute must retain A's recording")
	}
	b.HandlePeerEnded(a.PeerID)
	b.HandlePacket(resumed, silentPacket(680)) // stale in-flight RTP after leave
	b.HandlePeerEnded(a.PeerID)
	if len(records) != 2 || records[0].closes.Load() != 1 || transcribers[0].closes.Load() != 1 {
		t.Fatal("peer_left must close A exactly once without recreating sinks")
	}
	if records[1].closes.Load() != 0 {
		t.Fatal("A leaving closed B's recording")
	}
	b.Close()
	b.Close()
	b.HandlePacket(other, silentPacket(400480))
	if len(records) != 2 || records[1].closes.Load() != 1 || transcribers[1].closes.Load() != 1 {
		t.Fatal("shutdown must close remaining sinks exactly once and reject late RTP")
	}
}

func TestRecordTimelineNewBindingRebasesRTPWithoutLosingMutedGap(t *testing.T) {
	for _, timestamp := range []uint32{10, 900000000} {
		sink := &fakeSink{}
		r := newRecordTimeline(sink)
		t0 := time.Now()
		pcm := make([]byte, silenceBytes(10*time.Millisecond))
		r.sendPCM(pcm, 100000, t0, true)
		r.onTick(t0.Add(2 * time.Second)) // muted padding already committed
		r.sendPCM(pcm, timestamp, t0.Add(2100*time.Millisecond), true)
		r.sendPCM(pcm, timestamp+480, t0.Add(2110*time.Millisecond), false)
		if n, _ := sink.snapshot(); n != silenceBytes(2120*time.Millisecond) {
			t.Errorf("new timestamp %d: bytes=%d, want 2120ms (no dropped audio/huge RTP gap/double silence)", timestamp, n)
		}
		r.close(t0)
	}
}

func TestDecoupledBridgeConcurrentCreationAndShutdown(t *testing.T) {
	entered, unblock := make(chan struct{}), make(chan struct{})
	var unblockOnce sync.Once
	t.Cleanup(func() { unblockOnce.Do(func() { close(unblock) }) })
	var created atomic.Int32
	sink := &countingSink{}
	b := NewDecoupledBridge(func(rtcagent.TrackInfo) Sink {
		if created.Add(1) == 1 {
			close(entered)
		}
		<-unblock
		return sink
	}, nil)
	info := rtcagent.TrackInfo{Mid: "3", PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 1}
	var packets sync.WaitGroup
	packets.Add(2)
	go func() { defer packets.Done(); b.HandlePacket(info, silentPacket(1000)) }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("factory not called")
	}
	go func() {
		defer packets.Done()
		moved := info
		moved.Mid, moved.BindingRevision = "4", 2
		b.HandlePacket(moved, silentPacket(100))
	}()
	closed := make(chan struct{})
	go func() { b.HandlePeerEnded(info.PeerID); close(closed) }()
	unblockOnce.Do(func() { close(unblock) })
	packets.Wait()
	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("peer shutdown deadlocked with sink initialization")
	}
	b.Close()
	if created.Load() != 1 || sink.closes.Load() != 1 {
		t.Fatalf("created=%d closed=%d, want exactly one each", created.Load(), sink.closes.Load())
	}
}

func TestDecoupledBridgeSTTToggleDoesNotRestartRecording(t *testing.T) {
	record := &countingSink{}
	var stt []*countingSink
	b := NewDecoupledBridge(func(rtcagent.TrackInfo) Sink { return record }, func(rtcagent.TrackInfo) Sink {
		s := &countingSink{}
		stt = append(stt, s)
		return s
	})
	t.Cleanup(b.Close)
	info := rtcagent.TrackInfo{Mid: "3", PeerID: 10, Kind: rtcagent.KindAudio, BindingRevision: 1}
	b.HandlePacket(info, silentPacket(1000))
	b.HandleTrackEnded(info)
	b.SetSTTEnabled(true)
	b.SetSTTEnabled(true)
	if len(stt) != 1 {
		t.Fatal("repeated enable must create only one STT sink, even while muted")
	}
	b.SetSTTEnabled(false)
	if stt[0].closes.Load() != 1 || record.closes.Load() != 0 {
		t.Fatal("disabling STT should close only STT")
	}
	b.SetSTTEnabled(true)
	b.Close()
	b.SetSTTEnabled(true)
	if len(stt) != 2 || stt[1].closes.Load() != 1 || record.closes.Load() != 1 {
		t.Fatal("shutdown/toggle leaked or reopened a sink")
	}
}
