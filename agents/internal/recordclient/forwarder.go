package recordclient

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync/atomic"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/recordpb"
)

// SessionMeta is the SessionStart handshake for one forwarding session.
type SessionMeta struct {
	RoomID              string
	TrackID             string
	ParticipantIdentity string
	Source              string // "mic" | "screen"
	SampleRate          int32
	Channels            int32
}

// Forwarder is one forwarding session for one track. Not reused across
// tracks.
//
// SendPCM and Close must only ever be called from a single goroutine (never
// concurrently with each other) -- droppedSinceFlush/closed are plain
// fields, not synchronized, by design: the intended caller
// (internal/recording.Bridge) already guarantees this per track, see
// rtcagent.PeerAgent's OnAudioPacket/OnTrackEnded doc. rejected is the one
// exception -- it's written by the reader goroutine below and read from
// SendPCM's goroutine, so it's an atomic.
type Forwarder struct {
	stream  recordpb.RecordingIngest_StreamAudioClient
	trackID string

	queue             chan *recordpb.AudioChunk
	droppedSinceFlush int
	closed            bool
	rejected          atomic.Bool

	writerDone chan struct{}
	readerDone chan struct{}
}

// NewForwarder opens the bidi stream and starts a session for one track.
// Callers must treat a non-nil error as "no recording for this session",
// never as fatal to whatever produced the audio.
func NewForwarder(client *Client, meta SessionMeta, maxQueueSize int) (*Forwarder, error) {
	stream, err := client.stub.StreamAudio(context.Background())
	if err != nil {
		return nil, fmt.Errorf("recordclient: open stream for track=%s: %w", meta.TrackID, err)
	}

	f := &Forwarder{
		stream:     stream,
		trackID:    meta.TrackID,
		queue:      make(chan *recordpb.AudioChunk, maxQueueSize),
		writerDone: make(chan struct{}),
		readerDone: make(chan struct{}),
	}

	// Enqueued before the writer goroutine starts, so it's always the first
	// item on the wire regardless of how fast the caller starts sending PCM.
	f.queue <- &recordpb.AudioChunk{Payload: &recordpb.AudioChunk_Start{Start: &recordpb.SessionStart{
		RoomId:              meta.RoomID,
		TrackId:             meta.TrackID,
		ParticipantIdentity: meta.ParticipantIdentity,
		Source:              meta.Source,
		SampleRate:          meta.SampleRate,
		Channels:            meta.Channels,
	}}}

	go f.writeLoop()
	go f.readLoop()

	return f, nil
}

// SendPCM enqueues one PCM frame. Non-blocking: if the internal queue is
// full (record-service, or the network to it, can't keep up), the frame is
// dropped and counted rather than ever blocking the caller. The count is
// reported to record-service on the next successful send, or folded into
// the final Close() if it never recovers.
func (f *Forwarder) SendPCM(pcm []byte) {
	if f.closed || f.rejected.Load() {
		return
	}
	if f.droppedSinceFlush > 0 {
		select {
		case f.queue <- &recordpb.AudioChunk{Payload: &recordpb.AudioChunk_Dropped{Dropped: &recordpb.DroppedFrames{Count: int32(f.droppedSinceFlush)}}}:
			f.droppedSinceFlush = 0
		default:
			f.droppedSinceFlush++
			return
		}
	}
	select {
	case f.queue <- &recordpb.AudioChunk{Payload: &recordpb.AudioChunk_Pcm{Pcm: pcm}}:
	default:
		f.droppedSinceFlush++
	}
}

func (f *Forwarder) writeLoop() {
	defer close(f.writerDone)
	for item := range f.queue {
		if err := f.stream.Send(item); err != nil {
			logging.L.Warn("recordclient: write loop stopped", append(logging.ErrAttrs(err), "track_id", f.trackID)...)
			return
		}
	}
	_ = f.stream.CloseSend()
}

func (f *Forwarder) readLoop() {
	defer close(f.readerDone)
	for {
		ack, err := f.stream.Recv()
		if err != nil {
			if !errors.Is(err, io.EOF) {
				logging.L.Warn("recordclient: read loop stopped", append(logging.ErrAttrs(err), "track_id", f.trackID)...)
			}
			return
		}
		switch ack.GetStatus() {
		case "accepted":
			logging.L.Info("recordclient: session accepted", "track_id", f.trackID, "object_key", ack.GetObjectKey())
		case "rejected":
			f.rejected.Store(true)
			logging.L.Warn("recordclient: session rejected", "track_id", f.trackID, "error", ack.GetError())
		case "completed":
			logging.L.Info("recordclient: session completed", "track_id", f.trackID)
		}
	}
}

// Close flushes any pending drop count, signals the write loop to stop, and
// waits (briefly) for both loops to finish. Must be called from the same
// goroutine as SendPCM -- see the Forwarder doc.
func (f *Forwarder) Close() {
	if f.closed {
		return
	}
	f.closed = true

	if f.droppedSinceFlush > 0 {
		select {
		case f.queue <- &recordpb.AudioChunk{Payload: &recordpb.AudioChunk_Dropped{Dropped: &recordpb.DroppedFrames{Count: int32(f.droppedSinceFlush)}}}:
		default:
		}
		f.droppedSinceFlush = 0
	}
	close(f.queue)

	select {
	case <-f.writerDone:
	case <-time.After(5 * time.Second):
	}
	select {
	case <-f.readerDone:
	case <-time.After(5 * time.Second):
	}
}
