// Package audiopipeline Opus-decodes each mic-slot RTP packet and forwards
// the resulting 16kHz mono PCM to record-service when configured. See
// audio-ingestion/PLAN.md D3/D5/D12 for why decoding lives here (the agent
// is the only WebRTC client, so it decodes once and reuses the PCM for both
// consumers instead of each doing its own Opus decode).
package audiopipeline

import (
	"encoding/binary"
	"sync"
	"time"

	"github.com/pion/opus"
	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

const (
	// PCMSampleRate/PCMChannels match the old Python agent's AudioConfig
	// default (SAMPLE_RATE=16000, CHANNELS=1) that record-service assumes.
	// the magic numbers. pion/opus (a pure-Go RFC 6716 decoder, no
	// libopus/cgo needed) can decode straight to this rate -- no separate
	// resampling step required.
	PCMSampleRate = 16000
	PCMChannels   = 1
	// maxSamplesPerPacket bounds the decode buffer: RFC 6716 caps a single
	// Opus packet at 120ms; at 16kHz mono that's 1920 samples.
	maxSamplesPerPacket = 1920
	recordSilenceTick   = 100 * time.Millisecond
	rtpActivityGrace    = 200 * time.Millisecond
	maxSilenceChunk     = 2 * time.Second
	// WebRTC Opus RTP timestamps always use a 48kHz media clock, even though
	// this pipeline decodes packets to 16kHz PCM before forwarding them.
	opusRTPClockRate = 48000
)

// Sink receives decoded PCM for one track and is closed exactly once when
// the track ends. recordclient.Forwarder implements this directly.
type Sink interface {
	SendPCM(pcm []byte)
	Close()
}

// session is mostly owned by exactly one goroutine -- the rtcagent read
// loop for one track calls HandlePacket/HandleTrackEnded back-to-back,
// never concurrently with itself (see rtcagent.PeerAgent's
// OnAudioPacket/OnTrackEnded doc). decoder/pcmBuf need no lock for that
// reason. recordTimeline owns its own lock because its padding work runs on
// a background goroutine.
type session struct {
	info    rtcagent.TrackInfo
	decoder opus.Decoder
	pcmBuf  []int16

	record *recordTimeline // nil if recording disabled/failed to start
}

// Bridge owns legacy MID sessions or decoupled participant sessions.
type Bridge struct {
	// Decoupled rooms keep participant sinks across slot release/reassignment.
	// The legacy path below retains its existing track-based lifecycle.
	participants *participantBridge
	// newRecordSink builds a fresh Sink for a newly-seen track. It may be nil
	// to disable recording entirely (e.g.
	// newRecordSink is nil if dialing record-service failed at startup).
	// Returning nil means "couldn't start this sink for this
	// track" (already logged by the factory) -- session creation continues
	// regardless, best-effort per PLAN.md D5.
	newRecordSink func(info rtcagent.TrackInfo) Sink

	mu       sync.Mutex
	sessions map[string]*session
}

func NewBridge(newRecordSink func(info rtcagent.TrackInfo) Sink) *Bridge {
	return &Bridge{
		newRecordSink: newRecordSink,
		sessions:      make(map[string]*session),
	}
}

type recordTimeline struct {
	mu sync.Mutex

	sink        Sink
	closed      bool
	initialized bool
	// lastRTPTimestamp is the media timestamp of the first sample in the
	// previous packet. lastPacketRTPTicks is that packet's duration on the
	// mandatory 48kHz Opus RTP clock.
	lastRTPTimestamp   uint32
	lastPacketRTPTicks uint32
	// paddedSamplesSinceLastRTP counts 16kHz silence samples already sent by
	// onTick after the latest real packet. When another packet arrives its
	// RTP timestamp gives the exact gap, so sendAllPcm only fills the part
	// not already committed by the ticker.
	paddedSamplesSinceLastRTP int64
	lastRTPAt                 time.Time

	stop     chan struct{}
	done     chan struct{}
	stopOnce sync.Once
}

func newRecordTimeline(sink Sink) *recordTimeline {
	r := &recordTimeline{
		sink: sink,
		stop: make(chan struct{}),
		done: make(chan struct{}),
	}
	go r.run()
	return r
}

func (r *recordTimeline) run() {
	defer close(r.done)
	ticker := time.NewTicker(recordSilenceTick)
	defer ticker.Stop()

	for {
		select {
		case now := <-ticker.C:
			r.onTick(now)
		case <-r.stop:
			return
		}
	}
}

func (r *recordTimeline) sendAllPcm(pcm []byte, rtpTimestamp uint32, now time.Time) {
	r.sendPCM(pcm, rtpTimestamp, now, false)
}

// A new binding can have an unrelated RTP clock. Use elapsed arrival time
// only across that boundary; preserve RTP timing within a binding.
func (r *recordTimeline) sendPCM(pcm []byte, rtpTimestamp uint32, now time.Time, newBinding bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return
	}

	bytesPerSample := PCMChannels * 2
	sampleCount := int64(len(pcm) / bytesPerSample)
	if sampleCount <= 0 {
		return
	}
	packetRTPTicks := uint32(uint64(sampleCount) * opusRTPClockRate / PCMSampleRate)

	if r.initialized && newBinding {
		previousSamples := int64(r.lastPacketRTPTicks) * PCMSampleRate / opusRTPClockRate
		gap := durationToPCMSamples(now.Sub(r.lastRTPAt)) - previousSamples
		if remaining := gap - r.paddedSamplesSinceLastRTP; remaining > 0 {
			r.sendSilenceSamplesLocked(remaining)
		}
	} else if r.initialized {
		endOfLastPacket := r.lastRTPTimestamp + r.lastPacketRTPTicks
		// Converting the modular uint32 subtraction to int32 gives the usual
		// RTP serial-number ordering for differences below half the timestamp
		// space and naturally handles the uint32 wraparound.
		gapTicks := int32(rtpTimestamp - endOfLastPacket)
		if gapTicks >= 0 {
			requiredSilenceSamples := int64(gapTicks) * PCMSampleRate / opusRTPClockRate
			remainingSilenceSamples := requiredSilenceSamples - r.paddedSamplesSinceLastRTP
			if remainingSilenceSamples > 0 {
				r.sendSilenceSamplesLocked(remainingSilenceSamples)
			} else if remainingSilenceSamples < 0 {
				// The ticker committed farther than the subsequently observed RTP
				// media gap. An append-only PCM stream cannot retract those bytes;
				// retain real audio and make the grace-window violation visible.
				logging.L.Warn("audiopipeline: Too much silence padding",
					"rtp_timestamp", rtpTimestamp,
					"end_of_last_packet", endOfLastPacket,
					"required_silence_samples", requiredSilenceSamples,
					"already_padded_samples", r.paddedSamplesSinceLastRTP,
					"overlap_samples", -remainingSilenceSamples)
			}
		} else {
			// Do not turn a late/duplicate timestamp into a huge unsigned gap.
			// Recording it would also move the media cursor backward and make
			// the following in-order packet look like a new gap, so omit it from
			// the recording.
			logging.L.Warn("audiopipeline: late or overlapping RTP timestamp",
				"rtp_timestamp", rtpTimestamp,
				"end_of_last_packet", endOfLastPacket,
				"overlap_rtp_ticks", -int64(gapTicks))
			return
		}
	}

	r.sink.SendPCM(pcm)
	r.initialized = true
	r.lastRTPTimestamp = rtpTimestamp
	r.lastPacketRTPTicks = packetRTPTicks
	r.lastRTPAt = now
	r.paddedSamplesSinceLastRTP = 0
}

func (r *recordTimeline) onTick(now time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed || !r.initialized || r.lastRTPAt.IsZero() {
		return
	}
	elapsed := now.Sub(r.lastRTPAt)
	if elapsed < rtpActivityGrace {
		return
	}

	// Keep the most recent grace period uncommitted. If a delayed RTP packet
	// arrives inside that window it is forwarded normally. Count committed
	// silence in samples so sendAllPcm can reconcile it with the exact gap
	// revealed by the next RTP media timestamp.
	targetSamples := durationToPCMSamples(elapsed - rtpActivityGrace)
	samplesToSend := targetSamples - r.paddedSamplesSinceLastRTP
	if samplesToSend <= 0 {
		return
	}
	r.sendSilenceSamplesLocked(samplesToSend)
	r.paddedSamplesSinceLastRTP += samplesToSend
}

func durationToPCMSamples(duration time.Duration) int64 {
	if duration <= 0 {
		return 0
	}
	return duration.Nanoseconds() * int64(PCMSampleRate) / int64(time.Second)
}

func (r *recordTimeline) sendSilenceSamplesLocked(sampleCount int64) {
	if sampleCount <= 0 {
		return
	}

	maxChunkSamples := durationToPCMSamples(maxSilenceChunk)
	for sampleCount > 0 {
		chunkSamples := sampleCount
		if chunkSamples > maxChunkSamples {
			chunkSamples = maxChunkSamples
		}
		r.sink.SendPCM(make([]byte, int(chunkSamples)*PCMChannels*2))
		sampleCount -= chunkSamples
	}
}

func (r *recordTimeline) close(now time.Time) {
	r.stopOnce.Do(func() {
		close(r.stop)
		<-r.done
	})

	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	if r.initialized && !r.lastRTPAt.IsZero() {
		requiredTailSamples := durationToPCMSamples(now.Sub(r.lastRTPAt))
		remainingTailSamples := requiredTailSamples - r.paddedSamplesSinceLastRTP
		if remainingTailSamples > 0 {
			r.sendSilenceSamplesLocked(remainingTailSamples)
		}
	}
	r.closed = true
	sink := r.sink
	r.sink = nil
	r.mu.Unlock()

	sink.Close()
}

// HandlePacket decodes one Opus RTP packet and fans the PCM out to whatever
// sink this track has. Starts a new session (decoder + record-service
// sink) on the first packet for a
// given mid in legacy mode, or participant in decoupled mode.
func (b *Bridge) HandlePacket(info rtcagent.TrackInfo, pkt *rtp.Packet) {
	if b.participants != nil {
		b.participants.handlePacket(info, pkt)
		return
	}
	s := b.sessionFor(info)
	if s == nil {
		return
	}

	n, err := s.decoder.DecodeToInt16(pkt.Payload, s.pcmBuf)
	if err != nil {
		logging.L.Warn("audiopipeline: opus decode failed, dropping packet",
			append(logging.ErrAttrs(err), "mid", info.Mid, "user_id", info.UserID)...)
		return
	}
	pcm := int16ToLEBytes(s.pcmBuf[:n])

	if s.record != nil {
		s.record.sendAllPcm(pcm, pkt.Timestamp, time.Now())
	}
}

// HandleTrackEnded closes a legacy track session. In decoupled rooms it
// only retires the decoder binding; sinks survive until peer/session end.
func (b *Bridge) HandleTrackEnded(info rtcagent.TrackInfo) {
	if b.participants != nil {
		b.participants.endBinding(info)
		return
	}
	b.mu.Lock()
	s, ok := b.sessions[info.Mid]
	if ok && ((info.PeerID != 0 && s.info.PeerID != info.PeerID) ||
		(info.BindingRevision != 0 && s.info.BindingRevision != info.BindingRevision)) {
		b.mu.Unlock()
		return
	}
	delete(b.sessions, info.Mid)
	b.mu.Unlock()
	if !ok {
		return
	}

	b.finalizeSession(s)
	logging.L.Info("audiopipeline: stopped forwarding", "mid", info.Mid, "user_id", info.UserID)
}

// finalizeSession closes every sink s owns. Shared by HandleTrackEnded (the
// track itself ended) and sessionFor's slot-reassignment path (the mid is
// still alive -- mezon-sfu just handed it to a different peer, see
// rtcagent.PeerAgent.ApplySlotAssigned's doc).
func (b *Bridge) finalizeSession(s *session) {
	if s.record != nil {
		s.record.close(time.Now())
	}
}

func (b *Bridge) sessionFor(info rtcagent.TrackInfo) *session {
	b.mu.Lock()
	if s, ok := b.sessions[info.Mid]; ok {
		if s.info.PeerID == info.PeerID && s.info.BindingRevision == info.BindingRevision {
			b.mu.Unlock()
			return s
		}
		// The legacy fixed pool reassigned this MID without renegotiating
		// SDP (PR #475, max_active_speakers > 0). See
		// rtcagent.PeerAgent.ApplySlotAssigned. Same mid, same
		// transceiver/SSRC throughout, so this PeerID mismatch on an
		// already-live session is the only point that ever learns the
		// occupant changed. Finalize the outgoing peer's session before
		// starting a fresh one -- otherwise their audio and the new
		// occupant's would land in the same record-service track, the same
		// room_id:track_id collision class of bug PR #278/#291 fixed for
		// reconnects, now via slot reuse instead.
		delete(b.sessions, info.Mid)
		b.mu.Unlock()
		logging.L.Info("audiopipeline: mid binding changed, starting a new session",
			"mid", info.Mid, "old_user_id", s.info.UserID, "old_peer_id", s.info.PeerID,
			"old_binding_revision", s.info.BindingRevision, "new_user_id", info.UserID,
			"new_peer_id", info.PeerID, "new_binding_revision", info.BindingRevision)
		b.finalizeSession(s)
	} else {
		b.mu.Unlock()
	}

	dec, err := opus.NewDecoderWithOutput(PCMSampleRate, PCMChannels)
	if err != nil {
		logging.L.Error("audiopipeline: failed to create opus decoder", append(logging.ErrAttrs(err), "mid", info.Mid)...)
		return nil
	}

	s := &session{info: info, decoder: dec, pcmBuf: make([]int16, maxSamplesPerPacket)}

	if b.newRecordSink != nil {
		if sink := b.newRecordSink(info); sink != nil {
			s.record = newRecordTimeline(sink)
		}
	}
	b.mu.Lock()
	b.sessions[info.Mid] = s
	b.mu.Unlock()

	logging.L.Info("audiopipeline: started session", "mid", info.Mid, "user_id", info.UserID, "peer_id", info.PeerID,
		"recording", s.record != nil)
	return s
}

func int16ToLEBytes(samples []int16) []byte {
	out := make([]byte, len(samples)*2)
	for i, v := range samples {
		binary.LittleEndian.PutUint16(out[i*2:], uint16(v))
	}
	return out
}
