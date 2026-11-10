// Package audiopipeline wraps each mic track's existing Opus RTP payloads in
// Ogg for record-service. Decoding is retained only to determine each packet's
// media duration for exact silence padding.
package audiopipeline

import (
	"fmt"
	"sync"
	"time"

	"github.com/pion/opus"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4/pkg/media/oggwriter"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
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
	// WebRTC Opus RTP timestamps always use a 48kHz media clock, even though
	// this pipeline decodes packets to 16kHz PCM before forwarding them.
	opusRTPClockRate = 48000

	RecordOpusSampleRate    = opusRTPClockRate
	RecordOpusChannels      = 1
	maxSilenceFrameRTPTicks = RecordOpusSampleRate / 50  // 20ms
	minSilenceFrameRTPTicks = RecordOpusSampleRate / 400 // 2.5ms
)

// Sink receives consecutive fragments of a single physical Ogg object. That
// object may contain chained logical Ogg streams when an SFU binding/SSRC
// changes; concatenating the fragments in order produces the complete file.
type Sink interface {
	SendOgg(ogg []byte)
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

	sink           Sink
	oggWriter      *oggwriter.Writer
	oggTrack       *oggwriter.Track
	silenceEncoder opusenc.Encoder
	silencePCM     []int16
	ssrc           uint32
	closed         bool
	initialized    bool
	// lastRTPTimestamp is the media timestamp of the first sample in the
	// previous packet. lastPacketRTPTicks is that packet's duration on the
	// mandatory 48kHz Opus RTP clock.
	lastRTPTimestamp   uint32
	lastPacketRTPTicks uint32
	// paddedRTPTicksSinceLastRTP counts 48kHz silence ticks already sent by
	// onTick after the latest real packet. When another packet arrives its
	// RTP timestamp gives the exact gap, so sendAllOgg only fills the part
	// not already committed by the ticker.
	paddedRTPTicksSinceLastRTP int64
	lastRTPAt                  time.Time

	stop     chan struct{}
	done     chan struct{}
	stopOnce sync.Once
}

type oggSinkWriter struct {
	sink Sink
}

func (w *oggSinkWriter) Write(data []byte) (int, error) {
	// Pion owns data after Write returns; the asynchronous forwarder needs an
	// independent copy.
	w.sink.SendOgg(append([]byte(nil), data...))
	return len(data), nil
}

func newOggLogicalStream(sink Sink, ssrc uint32) (*oggwriter.Writer, *oggwriter.Track, error) {
	writer, err := oggwriter.NewWriter(
		&oggSinkWriter{sink: sink},
		oggwriter.WithSampleRate(RecordOpusSampleRate),
		oggwriter.WithChannelCount(RecordOpusChannels),
	)
	if err != nil {
		return nil, nil, fmt.Errorf("create Ogg writer: %w", err)
	}
	track, err := writer.NewTrack(ssrc)
	if err != nil {
		_ = writer.Close()
		return nil, nil, fmt.Errorf("create Ogg track: %w", err)
	}
	return writer, track, nil
}

func newRecordTimeline(sink Sink, ssrc uint32) (*recordTimeline, error) {
	silenceEncoder, err := opusenc.New(RecordOpusSampleRate, RecordOpusChannels)
	if err != nil {
		return nil, fmt.Errorf("create recording silence encoder: %w", err)
	}
	writer, track, err := newOggLogicalStream(sink, ssrc)
	if err != nil {
		return nil, err
	}
	r := &recordTimeline{
		sink:           sink,
		oggWriter:      writer,
		oggTrack:       track,
		silenceEncoder: silenceEncoder,
		silencePCM:     make([]int16, maxSilenceFrameRTPTicks*RecordOpusChannels),
		ssrc:           ssrc,
		stop:           make(chan struct{}),
		done:           make(chan struct{}),
	}
	go r.run()
	return r, nil
}

type oggStreamTransition struct {
	oldSSRC uint32
	newSSRC uint32
	reason  string
}

func (r *recordTimeline) run() {
	defer close(r.done)
	ticker := time.NewTicker(recordSilenceTick)
	defer ticker.Stop()

	for {
		select {
		case now := <-ticker.C:
			if err := r.onTick(now); err != nil {
				logging.L.Warn("audiopipeline: failed to write Ogg silence", logging.ErrAttrs(err)...)
			}
		case <-r.stop:
			return
		}
	}
}

// sendAllOgg writes the packet's existing Opus payload without re-encoding.
// A new SFU binding has an unrelated RTP clock, so it starts a chained logical
// Ogg stream even when the SFU happens to reuse the same SSRC.
func (r *recordTimeline) sendAllOgg(
	pkt *rtp.Packet,
	packetRTPTicks uint32,
	now time.Time,
	newBinding bool,
) (*oggStreamTransition, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil, nil
	}
	if packetRTPTicks == 0 {
		return nil, nil
	}

	var transition *oggStreamTransition
	if r.initialized && (newBinding || pkt.SSRC != r.ssrc) {
		reason := "ssrc_rotation"
		if newBinding {
			reason = "binding_change"
		}
		transition = &oggStreamTransition{oldSSRC: r.ssrc, newSSRC: pkt.SSRC, reason: reason}

		// RTP timestamps cannot be compared across bindings/SSRC domains. Use
		// arrival time only at this boundary, accounting for the old packet's
		// duration and any silence the ticker already committed.
		previousPacketDuration := time.Duration(r.lastPacketRTPTicks) * time.Second / opusRTPClockRate
		targetTicks := durationToRTPTicks(now.Sub(r.lastRTPAt) - previousPacketDuration)
		if err := r.padToTargetLocked(targetTicks); err != nil {
			return transition, fmt.Errorf("pad before Ogg stream transition: %w", err)
		}
		if err := r.rotateLogicalStreamLocked(pkt.SSRC); err != nil {
			return transition, err
		}
	}

	if r.initialized {
		endOfLastPacket := r.lastRTPTimestamp + r.lastPacketRTPTicks
		// Converting the modular uint32 subtraction to int32 gives the usual
		// RTP serial-number ordering for differences below half the timestamp
		// space and naturally handles the uint32 wraparound.
		gapTicks := int32(pkt.Timestamp - endOfLastPacket)
		if gapTicks >= 0 {
			remainingSilenceTicks := int64(gapTicks) - r.paddedRTPTicksSinceLastRTP
			if remainingSilenceTicks > 0 {
				if _, err := r.sendSilenceRTPTicksLocked(remainingSilenceTicks); err != nil {
					return transition, err
				}
			} else if remainingSilenceTicks < 0 {
				// The ticker committed farther than the subsequently observed RTP
				// media gap. An append-only Ogg stream cannot retract those pages;
				// retain real audio and make the grace-window violation visible.
				logging.L.Warn("audiopipeline: too much silence padding",
					"rtp_timestamp", pkt.Timestamp,
					"end_of_last_packet", endOfLastPacket,
					"required_silence_rtp_ticks", gapTicks,
					"already_padded_rtp_ticks", r.paddedRTPTicksSinceLastRTP,
					"overlap_rtp_ticks", -remainingSilenceTicks)
			}
		} else {
			// Do not turn a late/duplicate timestamp into a huge unsigned gap.
			// Recording it would also move the media cursor backward and make
			// the following in-order packet look like a new gap, so omit it from
			// the recording.
			logging.L.Warn("audiopipeline: late or overlapping RTP timestamp",
				"rtp_timestamp", pkt.Timestamp,
				"end_of_last_packet", endOfLastPacket,
				"overlap_rtp_ticks", -int64(gapTicks))
			return transition, nil
		}
	}

	if err := r.oggTrack.WriteRTP(pkt); err != nil {
		return transition, fmt.Errorf("mux real RTP packet: %w", err)
	}
	r.initialized = true
	r.lastRTPTimestamp = pkt.Timestamp
	r.lastPacketRTPTicks = packetRTPTicks
	r.lastRTPAt = now
	r.paddedRTPTicksSinceLastRTP = 0
	return transition, nil
}

func (r *recordTimeline) rotateLogicalStreamLocked(newSSRC uint32) error {
	newSilenceEncoder, err := opusenc.New(RecordOpusSampleRate, RecordOpusChannels)
	if err != nil {
		return fmt.Errorf("create silence encoder for Ogg stream transition: %w", err)
	}
	newWriter, newTrack, err := newOggLogicalStream(r.sink, newSSRC)
	if err != nil {
		return fmt.Errorf("create chained Ogg stream for SSRC %d: %w", newSSRC, err)
	}
	if err := r.oggWriter.Close(); err != nil {
		return fmt.Errorf("finalize Ogg stream for SSRC %d: %w", r.ssrc, err)
	}

	r.oggWriter = newWriter
	r.oggTrack = newTrack
	r.silenceEncoder = newSilenceEncoder
	r.ssrc = newSSRC
	r.initialized = false
	r.lastRTPTimestamp = 0
	r.lastPacketRTPTicks = 0
	r.paddedRTPTicksSinceLastRTP = 0
	r.lastRTPAt = time.Time{}
	return nil
}

func (r *recordTimeline) onTick(now time.Time) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed || !r.initialized || r.lastRTPAt.IsZero() {
		return nil
	}
	elapsed := now.Sub(r.lastRTPAt)
	if elapsed < rtpActivityGrace {
		return nil
	}

	// Keep the most recent grace period uncommitted. If a delayed RTP packet
	// arrives inside that window it is forwarded normally. The next RTP media
	// timestamp reconciles this provisional wall-clock padding exactly.
	return r.padToTargetLocked(durationToRTPTicks(elapsed - rtpActivityGrace))
}

func durationToRTPTicks(duration time.Duration) int64 {
	if duration <= 0 {
		return 0
	}
	return duration.Nanoseconds() * int64(opusRTPClockRate) / int64(time.Second)
}

func (r *recordTimeline) padToTargetLocked(targetTicks int64) error {
	ticksToSend := targetTicks - r.paddedRTPTicksSinceLastRTP
	if ticksToSend <= 0 {
		return nil
	}
	written, err := r.sendSilenceRTPTicksLocked(ticksToSend)
	r.paddedRTPTicksSinceLastRTP += written
	return err
}

func (r *recordTimeline) sendSilenceRTPTicksLocked(ticks int64) (int64, error) {
	var written int64
	for ticks >= minSilenceFrameRTPTicks {
		frameTicks := int64(maxSilenceFrameRTPTicks)
		for frameTicks > ticks {
			frameTicks /= 2
		}
		if frameTicks < minSilenceFrameRTPTicks {
			break
		}
		payload, err := r.silenceEncoder.Encode(r.silencePCM[:int(frameTicks)*RecordOpusChannels])
		if err != nil {
			return written, fmt.Errorf("encode Ogg silence: %w", err)
		}
		if err := r.oggTrack.WriteRTP(&rtp.Packet{
			Header:  rtp.Header{SSRC: r.ssrc},
			Payload: payload,
		}); err != nil {
			return written, fmt.Errorf("mux Ogg silence: %w", err)
		}
		ticks -= frameTicks
		written += frameTicks
	}
	return written, nil
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
		if err := r.padToTargetLocked(durationToRTPTicks(now.Sub(r.lastRTPAt))); err != nil {
			logging.L.Warn("audiopipeline: failed to finalize Ogg silence", logging.ErrAttrs(err)...)
		}
	}
	if err := r.oggWriter.Close(); err != nil {
		logging.L.Warn("audiopipeline: failed to finalize Ogg stream", logging.ErrAttrs(err)...)
	}
	r.closed = true
	sink := r.sink
	r.sink = nil
	r.mu.Unlock()

	sink.Close()
}

// HandlePacket wraps one Opus RTP packet into the recording's Ogg stream.
// It starts a new session (decoder + record-service sink) on the first packet for a
// given mid in legacy mode, or participant in decoupled mode.
func (b *Bridge) HandlePacket(info rtcagent.TrackInfo, pkt *rtp.Packet) {
	if b.participants != nil {
		b.participants.handlePacket(info, pkt)
		return
	}
	s := b.sessionFor(info, pkt.SSRC)
	if s == nil {
		return
	}

	n, err := s.decoder.DecodeToInt16(pkt.Payload, s.pcmBuf)
	if err != nil {
		logging.L.Warn("audiopipeline: opus decode failed, dropping packet",
			append(logging.ErrAttrs(err), "mid", info.Mid, "user_id", info.UserID)...)
		return
	}
	if s.record != nil {
		packetRTPTicks := uint32(uint64(n) * opusRTPClockRate / PCMSampleRate)
		transition, recordErr := s.record.sendAllOgg(pkt, packetRTPTicks, time.Now(), false)
		if transition != nil {
			logging.L.Info("audiopipeline: started chained Ogg stream",
				"mid", info.Mid, "user_id", info.UserID,
				"old_ssrc", transition.oldSSRC, "new_ssrc", transition.newSSRC,
				"reason", transition.reason)
		}
		if recordErr != nil {
			logging.L.Warn("audiopipeline: failed to mux recording packet",
				append(logging.ErrAttrs(recordErr), "mid", info.Mid, "user_id", info.UserID)...)
		}
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

func (b *Bridge) sessionFor(info rtcagent.TrackInfo, ssrc uint32) *session {
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
			record, recordErr := newRecordTimeline(sink, ssrc)
			if recordErr != nil {
				sink.Close()
				logging.L.Error("audiopipeline: failed to create Ogg recording stream",
					append(logging.ErrAttrs(recordErr), "mid", info.Mid, "user_id", info.UserID)...)
			} else {
				s.record = record
			}
		}
	}
	b.mu.Lock()
	b.sessions[info.Mid] = s
	b.mu.Unlock()

	logging.L.Info("audiopipeline: started session", "mid", info.Mid, "user_id", info.UserID, "peer_id", info.PeerID,
		"recording", s.record != nil)
	return s
}
