package ttsplayer

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"

	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4/pkg/media/oggwriter"

	"github.com/mezonai/mezon-call-translation/agents/internal/opusenc"
)

const pcmOggSSRC = 1

type pcmOggEncoder struct {
	encoder      opusenc.Encoder
	frameSamples int
	pendingPCM   []int16
	buf          bytes.Buffer
	writer       *oggwriter.Writer
	track        *oggwriter.Track
}

func newPCMOggEncoder(sampleRate int) (*pcmOggEncoder, error) {
	encoder, err := opusenc.New(sampleRate, 1)
	if err != nil {
		return nil, err
	}

	e := &pcmOggEncoder{
		encoder:      encoder,
		frameSamples: sampleRate / 50,
	}
	if e.frameSamples <= 0 {
		return nil, fmt.Errorf("invalid sample rate %d", sampleRate)
	}

	e.writer, err = oggwriter.NewWriter(
		&e.buf,
		oggwriter.WithSampleRate(uint32(sampleRate)),
		oggwriter.WithChannelCount(1),
	)
	if err != nil {
		return nil, err
	}
	e.track, err = e.writer.NewTrack(pcmOggSSRC)
	if err != nil {
		return nil, err
	}
	return e, nil
}

func (e *pcmOggEncoder) EncodePCM(pcm []byte) ([]byte, error) {
	if len(pcm)%2 != 0 {
		return nil, fmt.Errorf("invalid PCM16 byte count %d", len(pcm))
	}

	for offset := 0; offset < len(pcm); offset += 2 {
		e.pendingPCM = append(e.pendingPCM, int16(binary.LittleEndian.Uint16(pcm[offset:])))
	}
	if err := e.encodeFrames(); err != nil {
		return nil, err
	}
	return e.takeOgg(), nil
}

func (e *pcmOggEncoder) Close() ([]byte, error) {
	var encodeErr error
	if len(e.pendingPCM) > 0 {
		e.pendingPCM = append(e.pendingPCM, make([]int16, e.frameSamples-len(e.pendingPCM))...)
		encodeErr = e.encodeFrames()
	}
	closeErr := e.writer.Close()
	return e.takeOgg(), errors.Join(encodeErr, closeErr)
}

func (e *pcmOggEncoder) encodeFrames() error {
	encodedSamples := 0
	defer func() {
		if encodedSamples > 0 {
			copy(e.pendingPCM, e.pendingPCM[encodedSamples:])
			e.pendingPCM = e.pendingPCM[:len(e.pendingPCM)-encodedSamples]
		}
	}()

	for len(e.pendingPCM)-encodedSamples >= e.frameSamples {
		frame := e.pendingPCM[encodedSamples : encodedSamples+e.frameSamples]
		opusPayload, err := e.encoder.Encode(frame)
		if err != nil {
			return err
		}
		if err := e.track.WriteRTP(&rtp.Packet{
			Header:  rtp.Header{SSRC: pcmOggSSRC},
			Payload: opusPayload,
		}); err != nil {
			return err
		}
		encodedSamples += e.frameSamples
	}
	return nil
}

func (e *pcmOggEncoder) takeOgg() []byte {
	if e.buf.Len() == 0 {
		return nil
	}
	ogg := bytes.Clone(e.buf.Bytes())
	e.buf.Reset()
	return ogg
}
