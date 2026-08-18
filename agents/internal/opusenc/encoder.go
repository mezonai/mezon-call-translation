// Package opusenc defines the Opus encoder seam internal/ttsplayer needs to
// turn synthesized PCM into RTP-ready Opus payloads.
//
// There is currently no implementation behind this interface. pion/opus
// (used for decoding, see internal/audiopipeline) is decode-only as of
// v0.1.0 -- no exported Encoder type, only an unexported one inside
// internal/celt. The other option, hraban/opus, wraps libopus via cgo; the
// dev machine this was written on has no libopus headers installed
// (`pkg-config opus` fails), so a cgo implementation couldn't be built or
// verified here, and silently landing an unbuildable/untested file felt
// worse than an honest gap. New wires cleanly through -- callers get a
// clear error, not a crash or silent no-op -- so TTS publish-back
// (internal/ttsplayer) is otherwise complete and ready for a real encoder
// to be dropped in behind this interface (most likely hraban/opus behind a
// cgo build tag once a build environment has libopus, or pion/opus directly
// if it grows encode support).
package opusenc

import "errors"

var ErrUnavailable = errors.New("opusenc: no Opus encoder implementation wired in yet (see package doc)")

// Encoder turns one frame of PCM (interleaved int16 samples, frame duration
// determined by len(pcm)/channels/sampleRate) into an Opus payload.
type Encoder interface {
	Encode(pcm []int16) ([]byte, error)
}

// New returns ErrUnavailable -- see package doc.
func New(sampleRate, channels int) (Encoder, error) {
	return nil, ErrUnavailable
}
