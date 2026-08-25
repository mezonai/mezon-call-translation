package opusenc

import "testing"

// TestEncodeRoundTrip is a smoke test for the cgo/libopus link actually
// working in this environment -- a clean `go build` with libopus-dev
// installed only proves it compiles and links, not that opus_encoder_create/
// opus_encode behave correctly at runtime.
func TestEncodeRoundTrip(t *testing.T) {
	enc, err := New(24000, 1)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	frame := make([]int16, 24000/50) // 20ms at 24kHz mono
	for i := range frame {
		// Cheap non-silent test signal so the encoder has something to do
		// (a real Opus encoder can special-case all-zero input).
		frame[i] = int16((i % 200) * 100)
	}

	payload, err := enc.Encode(frame)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if len(payload) == 0 {
		t.Fatal("Encode returned an empty payload")
	}
	if len(payload) > maxOpusPacketBytes {
		t.Fatalf("Encode returned %d bytes, want <= %d", len(payload), maxOpusPacketBytes)
	}
	t.Logf("encoded 20ms @24kHz mono frame -> %d byte Opus payload", len(payload))

	// Encode a second frame to make sure the reused internal buffer (see
	// encoder.buf) doesn't corrupt the first payload already returned.
	first := append([]byte(nil), payload...)
	if _, err := enc.Encode(frame); err != nil {
		t.Fatalf("second Encode: %v", err)
	}
	for i := range first {
		if payload[i] != first[i] {
			t.Fatalf("first payload mutated after second Encode call at byte %d: got %x, want %x", i, payload[i], first[i])
		}
	}
}
