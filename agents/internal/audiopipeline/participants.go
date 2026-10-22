package audiopipeline

import (
	"sync"
	"time"

	"github.com/pion/opus"
	"github.com/pion/rtp"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

type participantKey struct {
	peerID uint64
	kind   rtcagent.TrackKind
}

// decoderBinding is transport state; replacing it never replaces the sinks.
type decoderBinding struct {
	info    rtcagent.TrackInfo
	decoder opus.Decoder
	pcm     []int16
	started bool
}

type participantSession struct {
	// Serializes decoder use, sink initialization, sends, STT toggles and close.
	mu          sync.Mutex
	info        rtcagent.TrackInfo // stable participant identity for sink metadata
	initialized bool
	closed      bool
	revision    uint64
	binding     *decoderBinding
	record      *recordTimeline
	stt         Sink
}

type participantBridge struct {
	mu            sync.Mutex
	closed        bool
	sttEnabled    bool
	sessions      map[participantKey]*participantSession
	departed      map[uint64]bool
	retired       map[string]uint64 // highest ended revision per MID
	newRecordSink func(rtcagent.TrackInfo) Sink
	newSTTSink    func(rtcagent.TrackInfo) Sink
}

// NewDecoupledBridge gives each peer/kind one recording for this receiver
// session. Muted/VAD-displaced peers retain their sinks and silence timeline.
func NewDecoupledBridge(record, stt func(rtcagent.TrackInfo) Sink) *Bridge {
	return &Bridge{participants: &participantBridge{
		sessions: make(map[participantKey]*participantSession),
		departed: make(map[uint64]bool), retired: make(map[string]uint64),
		newRecordSink: record, newSTTSink: stt,
	}}
}

func (b *participantBridge) acceptsLocked(info rtcagent.TrackInfo) bool {
	if b.closed || b.departed[info.PeerID] || info.PeerID == 0 {
		return false
	}
	revision, retired := b.retired[info.Mid]
	return !retired || info.BindingRevision > revision
}

func (b *participantBridge) handlePacket(info rtcagent.TrackInfo, pkt *rtp.Packet) {
	b.mu.Lock()
	if !b.acceptsLocked(info) {
		b.mu.Unlock()
		return
	}
	key := participantKey{info.PeerID, info.Kind}
	s := b.sessions[key]
	if s == nil {
		s = &participantSession{info: info}
		b.sessions[key] = s // reserve before any network I/O; create sinks once
	}
	b.mu.Unlock()

	s.mu.Lock()
	defer s.mu.Unlock()
	b.mu.Lock()
	valid, sttEnabled := b.acceptsLocked(info), b.sttEnabled
	b.mu.Unlock()
	if !valid || s.closed || info.BindingRevision < s.revision {
		return
	}
	if s.binding == nil || s.binding.info.Mid != info.Mid || s.binding.info.BindingRevision != info.BindingRevision {
		decoder, err := opus.NewDecoderWithOutput(PCMSampleRate, PCMChannels)
		if err != nil {
			logging.L.Error("audiopipeline: cannot create binding decoder", logging.ErrAttrs(err)...)
			return
		}
		s.binding = &decoderBinding{info: info, decoder: decoder, pcm: make([]int16, maxSamplesPerPacket)}
		s.revision = info.BindingRevision
	}
	d := s.binding
	n, err := d.decoder.DecodeToInt16(pkt.Payload, d.pcm)
	if err != nil {
		logging.L.Warn("audiopipeline: opus decode failed", logging.ErrAttrs(err)...)
		return
	}
	if !s.initialized {
		s.initialized = true
		if b.newRecordSink != nil {
			if sink := b.newRecordSink(s.info); sink != nil {
				s.record = newRecordTimeline(sink)
			}
		}
		if sttEnabled && b.newSTTSink != nil {
			s.stt = b.newSTTSink(s.info)
		}
	}
	pcm := int16ToLEBytes(d.pcm[:n])
	if s.record != nil {
		s.record.sendPCM(pcm, pkt.Timestamp, time.Now(), !d.started)
	}
	d.started = true
	if s.stt != nil {
		s.stt.SendPCM(pcm)
	}
}

func (b *participantBridge) endBinding(info rtcagent.TrackInfo) {
	// A read-loop end with no owner is handled by full bridge shutdown. Never
	// let its zero identity retire a newer assignment on this MID.
	if info.PeerID == 0 {
		return
	}
	b.mu.Lock()
	if previous, ok := b.retired[info.Mid]; !ok || info.BindingRevision > previous {
		b.retired[info.Mid] = info.BindingRevision
	}
	s := b.sessions[participantKey{info.PeerID, info.Kind}]
	b.mu.Unlock()
	if s != nil {
		s.mu.Lock()
		if d := s.binding; d != nil && d.info.Mid == info.Mid && d.info.BindingRevision == info.BindingRevision {
			s.binding = nil
		}
		s.mu.Unlock()
	}
}

func (s *participantSession) close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return
	}
	s.closed = true
	s.binding = nil
	if s.record != nil {
		s.record.close(time.Now())
		s.record = nil
	}
	if s.stt != nil {
		s.stt.Close()
		s.stt = nil
	}
}

// HandlePeerEnded blocks late packets from recreating a departed peer's sinks.
// It is a no-op for legacy bridges, whose existing track lifecycle is retained.
func (b *Bridge) HandlePeerEnded(peerID uint64) {
	p := b.participants
	if p == nil {
		return
	}
	p.mu.Lock()
	p.departed[peerID] = true
	var ended []*participantSession
	for key, s := range p.sessions {
		if key.peerID == peerID {
			ended = append(ended, s)
			delete(p.sessions, key)
		}
	}
	p.mu.Unlock()
	for _, s := range ended {
		s.close()
	}
}

// Close is called after stopping WebRTC, including before a new joined setup.
func (b *Bridge) Close() {
	if p := b.participants; p != nil {
		p.mu.Lock()
		p.closed = true
		var ended []*participantSession
		for _, s := range p.sessions {
			ended = append(ended, s)
		}
		clear(p.sessions)
		p.mu.Unlock()
		for _, s := range ended {
			s.close()
		}
		return
	}
	b.mu.Lock()
	var ended []*session
	for _, s := range b.sessions {
		ended = append(ended, s)
	}
	clear(b.sessions)
	b.mu.Unlock()
	for _, s := range ended {
		b.finalizeSession(s)
	}
}

func (b *participantBridge) setSTTEnabled(enabled bool) {
	b.mu.Lock()
	b.sttEnabled = enabled
	var sessions []*participantSession
	for _, s := range b.sessions {
		sessions = append(sessions, s)
	}
	b.mu.Unlock()
	for _, s := range sessions {
		s.mu.Lock()
		// Read the latest intent after acquiring the session lock. A slow
		// enable must not overwrite a concurrent disable or peer departure.
		b.mu.Lock()
		want := b.sttEnabled && !b.closed && !b.departed[s.info.PeerID]
		b.mu.Unlock()
		if !s.closed && s.initialized {
			if want && s.stt == nil && b.newSTTSink != nil {
				s.stt = b.newSTTSink(s.info)
			} else if !want && s.stt != nil {
				s.stt.Close()
				s.stt = nil
			}
		}
		s.mu.Unlock()
	}
}
