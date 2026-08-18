// Package ttsplayer publishes synthesized speech into a mezon-sfu room.
// Ported from the old Python agent's TTSManager
// (agents/src/core/tts_manager.py), narrowed to what
// mezon-sfu-migration-plan.md 2.5 actually scopes: synthesize -> Opus
// encode -> publish via a pion TrackLocalStaticSample, plus (best-effort)
// forwarding the same audio to record-service so the bot's own speech is
// captured too.
//
// NOT ported (interview-flow/orchestrator-recording-pipeline specific, out
// of scope for this pass):
//   - the silence-gap ticker that pads record-service's timeline through
//     long silent gaps between utterances so the recorded file's duration
//     tracks wall-clock time;
//   - reporting each utterance as a transcript segment / "TTS completed" to
//     orchestrator's /api/v2/recordings/events (replaces a Whisper job for
//     this track);
//   - TTS status push-back to room participants (was LiveKit DataChannel
//     `tts_status` -- mezon-sfu has no data channel yet).
package ttsplayer

import (
	"context"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordclient"
	"github.com/mezonai/mezon-call-translation/agents/internal/ttsclient"
)

const (
	frameDuration     = 20 * time.Millisecond
	speakQueueSize    = 8
	synthesizeTimeout = 30 * time.Second
)

type speakRequest struct {
	text, voice string
	speed       float64
}

// Player owns one publish track for the lifetime of one mezon-sfu session
// (like rtcagent.PeerAgent, not reused across reconnects).
type Player struct {
	track      *webrtc.TrackLocalStaticSample
	tts        *ttsclient.Client
	encoder    opusenc.Encoder
	sampleRate int

	recClient    *recordclient.Client
	roomID       string
	trackID      string
	participant  string
	maxQueueSize int

	mu        sync.Mutex
	forwarder *recordclient.Forwarder // lazily created on first utterance, lives for the Player's whole life

	requests chan speakRequest
	done     chan struct{}
}

// New builds a Player. track must already be attached to the
// PeerConnection (rtcagent.New's publishTrack parameter, role "speaker")
// before the first offer is answered. recClient may be nil (recording
// disabled/unavailable), matching internal/audiopipeline's convention.
//
// Returns opusenc.ErrUnavailable until a real Opus encoder is wired in
// (see that package's doc) -- callers should treat that as "TTS unavailable
// this session", not fatal to joining the room as a speaker (attaching the
// publish track itself doesn't need a working encoder, only Speak does).
func New(
	track *webrtc.TrackLocalStaticSample,
	ttsClient *ttsclient.Client,
	sampleRate int,
	recClient *recordclient.Client,
	roomID uint64,
	agentUserID int64,
	maxQueueSize int,
) (*Player, error) {
	enc, err := opusenc.New(sampleRate, 1)
	if err != nil {
		return nil, fmt.Errorf("ttsplayer: %w", err)
	}

	p := &Player{
		track:        track,
		tts:          ttsClient,
		encoder:      enc,
		sampleRate:   sampleRate,
		recClient:    recClient,
		roomID:       strconv.FormatUint(roomID, 10),
		trackID:      "agent-tts",
		participant:  strconv.FormatInt(agentUserID, 10),
		maxQueueSize: maxQueueSize,
		requests:     make(chan speakRequest, speakQueueSize),
		done:         make(chan struct{}),
	}
	go p.processQueue()
	return p, nil
}

// Speak queues one utterance for playback. Non-blocking, and safe to call
// from the SSE request-handling goroutine (internal/orchestratorclient)
// directly: a background worker plays queued utterances one at a time
// (mirrors the old Python TTSManager's request queue -- "only one audio
// stream active at a time"), so Speak never blocks on synthesis or on a
// previous utterance still playing. If the queue is full, the request is
// dropped and logged rather than blocking or growing unbounded.
func (p *Player) Speak(text, voice string, speed float64) {
	select {
	case p.requests <- speakRequest{text: text, voice: voice, speed: speed}:
	default:
		logging.L.Warn("ttsplayer: request queue full, dropping tts_play request", "text_preview", previewText(text))
	}
}

func (p *Player) processQueue() {
	defer close(p.done)
	for req := range p.requests {
		ctx, cancel := context.WithTimeout(context.Background(), synthesizeTimeout)
		err := p.speakNow(ctx, req.text, req.voice, req.speed)
		cancel()
		if err != nil {
			logging.L.Error("ttsplayer: failed to speak", append(logging.ErrAttrs(err), "text_preview", previewText(req.text))...)
		}
	}
}

func (p *Player) speakNow(ctx context.Context, text, voice string, speed float64) error {
	pcm, err := p.tts.Synthesize(ctx, text, voice, speed)
	if err != nil {
		return fmt.Errorf("synthesize: %w", err)
	}

	frameSamples := p.sampleRate / 50 // 20ms
	if frameSamples <= 0 {
		return fmt.Errorf("invalid sample rate %d", p.sampleRate)
	}

	for off := 0; off+frameSamples <= len(pcm); off += frameSamples {
		frame := pcm[off : off+frameSamples]

		opusPayload, err := p.encoder.Encode(frame)
		if err != nil {
			return fmt.Errorf("opus encode: %w", err)
		}
		if err := p.track.WriteSample(media.Sample{Data: opusPayload, Duration: frameDuration}); err != nil {
			return fmt.Errorf("write sample: %w", err)
		}
		p.forwardToRecordService(int16ToLEBytes(frame))
	}
	return nil
}

// forwardToRecordService lazily starts the forwarder on first use.
// Guarded by p.mu because, unlike internal/audiopipeline's per-track
// sessions (one goroutine owns a forwarder for its whole life), this
// forwarder is touched by both processQueue's single worker goroutine and
// Close (called from session teardown, a different goroutine) -- real
// mutual exclusion, not just goroutine affinity, matters here.
func (p *Player) forwardToRecordService(pcm []byte) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.recClient == nil {
		return
	}
	if p.forwarder == nil {
		fwd, err := recordclient.NewForwarder(p.recClient, recordclient.SessionMeta{
			RoomID:              p.roomID,
			TrackID:             p.trackID,
			ParticipantIdentity: p.participant,
			Source:              "mic",
			SampleRate:          int32(p.sampleRate),
			Channels:            1,
		}, p.maxQueueSize)
		if err != nil {
			logging.L.Error("ttsplayer: failed to start record-service forwarder", logging.ErrAttrs(err)...)
			p.recClient = nil // best-effort per PLAN.md D5: don't retry every frame after one failure
			return
		}
		p.forwarder = fwd
	}
	p.forwarder.SendPCM(pcm)
}

// Close stops accepting new requests, waits (briefly) for any in-flight
// utterance to finish, and closes the record-service forwarder if one was
// started.
func (p *Player) Close() {
	close(p.requests)
	select {
	case <-p.done:
	case <-time.After(35 * time.Second): // generous: worst case one queued utterance plus one in flight
	}

	p.mu.Lock()
	fwd := p.forwarder
	p.forwarder = nil
	p.mu.Unlock()
	if fwd != nil {
		fwd.Close()
	}
}

func previewText(text string) string {
	const maxLen = 50
	if len(text) <= maxLen {
		return text
	}
	return text[:maxLen] + "..."
}

func int16ToLEBytes(samples []int16) []byte {
	out := make([]byte, len(samples)*2)
	for i, v := range samples {
		out[i*2] = byte(v)
		out[i*2+1] = byte(v >> 8)
	}
	return out
}
