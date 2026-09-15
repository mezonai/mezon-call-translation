// Package audiopipeline Opus-decodes each mic-slot RTP packet once and fans
// the resulting 16kHz mono PCM out to whichever consumers want it --
// currently record-service (internal/recordclient, always on when
// configured) and STT (internal/sttclient, gated by SetSTTEnabled). See
// audio-ingestion/PLAN.md D3/D5/D12 for why decoding lives here (the agent
// is the only WebRTC client, so it decodes once and reuses the PCM for both
// consumers instead of each doing its own Opus decode).
package audiopipeline

import (
	"encoding/binary"
	"sync"
	"sync/atomic"

	"github.com/pion/opus"
	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

const (
	// PCMSampleRate/PCMChannels match the old Python agent's AudioConfig
	// default (SAMPLE_RATE=16000, CHANNELS=1) that record-service and STT
	// both assume -- exported so cmd/agent can use the same values when
	// building SessionStart/STT connection params instead of duplicating
	// the magic numbers. pion/opus (a pure-Go RFC 6716 decoder, no
	// libopus/cgo needed) can decode straight to this rate -- no separate
	// resampling step required.
	PCMSampleRate = 16000
	PCMChannels   = 1
	// maxSamplesPerPacket bounds the decode buffer: RFC 6716 caps a single
	// Opus packet at 120ms; at 16kHz mono that's 1920 samples.
	maxSamplesPerPacket = 1920
)

// Sink receives decoded PCM for one track and is closed exactly once, when
// the track ends (or, for the STT sink, when transcription is disabled
// mid-track). recordclient.Forwarder and sttclient.Client both implement
// this directly -- no adapter needed.
type Sink interface {
	SendPCM(pcm []byte)
	Close()
}

// session is mostly owned by exactly one goroutine -- the rtcagent read
// loop for one track calls HandlePacket/HandleTrackEnded back-to-back,
// never concurrently with itself (see rtcagent.PeerAgent's
// OnAudioPacket/OnTrackEnded doc). decoder/pcmBuf/recordSink need no lock
// for that reason. sttSink is the one exception: SetSTTEnabled can attach
// or detach it from a *different* goroutine (whatever's handling the
// `transcript_control` SSE request), asynchronously, mid-track -- so it's
// guarded by mu.
type session struct {
	info    rtcagent.TrackInfo
	decoder opus.Decoder
	pcmBuf  []int16

	recordSink Sink // nil if recording disabled/failed to start; immutable after creation

	mu      sync.Mutex
	sttSink Sink // nil unless STT is currently enabled for this track
}

// Bridge owns one session per audio track, keyed by mid.
type Bridge struct {
	// newRecordSink/newSTTSink build a fresh Sink for a newly-seen track;
	// either may be nil to disable that consumer entirely (e.g.
	// newRecordSink is nil if dialing record-service failed at startup).
	// Returning nil from either means "couldn't start this sink for this
	// track" (already logged by the factory) -- session creation continues
	// regardless, best-effort per PLAN.md D5.
	newRecordSink func(info rtcagent.TrackInfo) Sink
	newSTTSink    func(info rtcagent.TrackInfo) Sink

	sttEnabled atomic.Bool // mirrors the old Python agent's AgentControlState.transcription_enabled -- starts false, see SetSTTEnabled

	mu       sync.Mutex
	sessions map[string]*session
}

func NewBridge(newRecordSink, newSTTSink func(info rtcagent.TrackInfo) Sink) *Bridge {
	return &Bridge{
		newRecordSink: newRecordSink,
		newSTTSink:    newSTTSink,
		sessions:      make(map[string]*session),
	}
}

// HandlePacket decodes one Opus RTP packet and fans the PCM out to whatever
// sinks this track has. Starts a new session (decoder + record-service
// sink, and an STT sink if currently enabled) on the first packet for a
// given mid.
func (b *Bridge) HandlePacket(info rtcagent.TrackInfo, pkt *rtp.Packet) {
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

	if s.recordSink != nil {
		s.recordSink.SendPCM(pcm)
	}
	s.mu.Lock()
	stt := s.sttSink
	s.mu.Unlock()
	if stt != nil {
		stt.SendPCM(pcm)
	}
}

// HandleTrackEnded closes every sink for a track and forgets its session.
func (b *Bridge) HandleTrackEnded(info rtcagent.TrackInfo) {
	b.mu.Lock()
	s, ok := b.sessions[info.Mid]
	delete(b.sessions, info.Mid)
	b.mu.Unlock()
	if !ok {
		return
	}

	if s.recordSink != nil {
		s.recordSink.Close()
	}
	s.mu.Lock()
	stt := s.sttSink
	s.sttSink = nil
	s.mu.Unlock()
	if stt != nil {
		stt.Close()
	}
	logging.L.Info("audiopipeline: stopped forwarding", "mid", info.Mid, "user_id", info.UserID)
}

// SetSTTEnabled turns STT on/off for every currently-active track (and, via
// sttEnabled, for tracks that start after this call). Wire to the
// `transcript_control` SSE request handler (orchestratorclient). Ported
// behavior from the old Python agent's AgentControlState +
// start/stop_transcription_for_all: disabled by default, and toggling
// affects tracks already flowing, not just future ones.
func (b *Bridge) SetSTTEnabled(enabled bool) {
	b.sttEnabled.Store(enabled)

	b.mu.Lock()
	sessions := make([]*session, 0, len(b.sessions))
	for _, s := range b.sessions {
		sessions = append(sessions, s)
	}
	b.mu.Unlock()

	for _, s := range sessions {
		b.applySTTEnabled(s, enabled)
	}

	logging.L.Info("audiopipeline: stt enabled changed", "enabled", enabled, "affected_tracks", len(sessions))
}

func (b *Bridge) applySTTEnabled(s *session, enabled bool) {
	if !enabled {
		s.mu.Lock()
		sink := s.sttSink
		s.sttSink = nil
		s.mu.Unlock()
		if sink != nil {
			sink.Close()
		}
		return
	}

	if b.newSTTSink == nil {
		return
	}
	s.mu.Lock()
	already := s.sttSink != nil
	s.mu.Unlock()
	if already {
		return
	}

	sink := b.newSTTSink(s.info) // built outside the lock: may dial a WS connection
	if sink == nil {
		return
	}

	s.mu.Lock()
	if s.sttSink == nil {
		s.sttSink = sink
	} else {
		// Lost a race with a concurrent enabler (sessionFor for a track
		// that just started, or another SetSTTEnabled call) -- discard the
		// duplicate rather than leak/overwrite an in-use sink.
		s.mu.Unlock()
		sink.Close()
		return
	}
	s.mu.Unlock()
}

func (b *Bridge) sessionFor(info rtcagent.TrackInfo) *session {
	b.mu.Lock()
	if s, ok := b.sessions[info.Mid]; ok {
		b.mu.Unlock()
		return s
	}
	b.mu.Unlock()

	dec, err := opus.NewDecoderWithOutput(PCMSampleRate, PCMChannels)
	if err != nil {
		logging.L.Error("audiopipeline: failed to create opus decoder", append(logging.ErrAttrs(err), "mid", info.Mid)...)
		return nil
	}

	s := &session{info: info, decoder: dec, pcmBuf: make([]int16, maxSamplesPerPacket)}

	if b.newRecordSink != nil {
		s.recordSink = b.newRecordSink(info)
	}
	if b.sttEnabled.Load() && b.newSTTSink != nil {
		s.sttSink = b.newSTTSink(info)
	}

	b.mu.Lock()
	b.sessions[info.Mid] = s
	b.mu.Unlock()

	logging.L.Info("audiopipeline: started session", "mid", info.Mid, "user_id", info.UserID, "peer_id", info.PeerID,
		"recording", s.recordSink != nil, "stt", s.sttSink != nil)
	return s
}

func int16ToLEBytes(samples []int16) []byte {
	out := make([]byte, len(samples)*2)
	for i, v := range samples {
		binary.LittleEndian.PutUint16(out[i*2:], uint16(v))
	}
	return out
}
