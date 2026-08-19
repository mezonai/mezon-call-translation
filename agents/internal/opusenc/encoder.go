// Package opusenc turns synthesized PCM into RTP-ready Opus payloads for
// internal/ttsplayer.
//
// Backed by gopkg.in/hraban/opus.v2, the de-facto-standard Go binding for
// libopus (352 stars at the time this was written, MIT, the binding most of
// the Go/pion WebRTC ecosystem reaches for -- referenced directly in
// pion/webrtc's own discussions as the go-to for Opus encoding alongside
// Pion). Two cgo-free alternatives were tried and rejected first:
//   - pion/opus (used for decoding, see internal/audiopipeline) is
//     decode-only as of the version pinned in go.mod -- no exported
//     Encoder type.
//   - A WASM build of libopus run through tetratelabs/wazero (e.g.
//     github.com/jj11hh/opus, github.com/godeps/opus) avoids the cgo/
//     libopus-dev build requirement entirely and was verified working
//     here first. But every such wrapper found is a small, single-
//     maintainer fork (single digit stars) of this same hraban/opus -- more
//     supply-chain/staleness risk than the reputation this component
//     deserves for something that ships spoken audio into every call. Given
//     a real choice, reputation won over "no cgo needed" once libopus-dev
//     became available to build/verify against (`sudo apt-get install
//     libopus-dev libopusfile-dev` on this machine).
//
// This is why the Encoder interface below is deliberately kept tiny and
// implementation-agnostic: if the cgo/libopus-dev build requirement ever
// becomes a real deployment blocker, swapping back to one of the WASM
// options is a contained change behind this interface, not a rewrite of
// internal/ttsplayer.
package opusenc

import (
	"fmt"

	opus "gopkg.in/hraban/opus.v2"
)

// maxOpusPacketBytes bounds the encoder's per-frame output buffer. libopus's
// own opus_encode() documentation recommends 4000 bytes as a safe upper
// bound for the output buffer regardless of bitrate/complexity/frame size.
const maxOpusPacketBytes = 4000

// Encoder turns one frame of PCM (interleaved int16 samples, frame duration
// determined by len(pcm)/channels/sampleRate) into an Opus payload.
type Encoder interface {
	Encode(pcm []int16) ([]byte, error)
}

type encoder struct {
	enc *opus.Encoder
	buf []byte // reused across Encode calls; results are copied out, see Encode
}

// New creates an Opus encoder for sampleRate/channels, tuned for voice
// (opus.AppVoIP -- this is TTS speech, not music). sampleRate must be one
// of the rates libopus accepts natively (8000/12000/16000/24000/48000);
// internal/config's TTS_SAMPLE_RATE default (24000) is one of them, so no
// resampling step is needed before Encode. Note this is independent of the
// RTP-level "channels" WebRTC negotiates (always 2 for Opus per RFC 7587,
// see cmd/agent's publish-track comment) -- this channels is the actual
// number of channels in the PCM ttsplayer feeds in (1, TTS output is mono).
func New(sampleRate, channels int) (Encoder, error) {
	enc, err := opus.NewEncoder(sampleRate, channels, opus.AppVoIP)
	if err != nil {
		return nil, fmt.Errorf("opusenc: %w", err)
	}
	return &encoder{enc: enc, buf: make([]byte, maxOpusPacketBytes)}, nil
}

func (e *encoder) Encode(pcm []int16) ([]byte, error) {
	n, err := e.enc.Encode(pcm, e.buf)
	if err != nil {
		return nil, fmt.Errorf("opusenc: encode: %w", err)
	}
	// e.buf is reused on the next call -- callers (ttsplayer hands this
	// straight to pion's WriteSample, which may not consume it
	// synchronously) need their own copy, not a view into it.
	out := make([]byte, n)
	copy(out, e.buf[:n])
	return out, nil
}
