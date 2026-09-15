// Package ttsclient calls the TTS service's HTTP API to synthesize text.
// Ported from the old Python agent's process_text_to_audio
// (agents/src/services/tts_client.py). The response body is raw S16LE PCM
// (mono, at whatever sample rate the TTS service was configured for --
// Kokoro's default is 24kHz; not returned in the response, so the caller
// must already know it -- see internal/ttsplayer).
package ttsclient

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func New(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// Synthesize returns the synthesized audio as S16LE PCM samples. voice/speed
// are omitted from the request when zero-valued (mirrors the old Python
// client dropping None fields from the payload).
func (c *Client) Synthesize(ctx context.Context, text, voice string, speed float64) ([]int16, error) {
	if c.baseURL == "" {
		return nil, fmt.Errorf("ttsclient: base URL not configured")
	}

	payload := map[string]any{"text": text}
	if voice != "" {
		payload["voice"] = voice
	}
	if speed != 0 {
		payload["speed"] = speed
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("ttsclient: encode request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/tts/process", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("ttsclient: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("ttsclient: request: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("ttsclient: read response: %w", err)
	}
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return nil, fmt.Errorf("ttsclient: HTTP %d: %s", resp.StatusCode, string(raw))
	}
	if len(raw)%2 != 0 {
		return nil, fmt.Errorf("ttsclient: response length %d is not a whole number of S16LE samples", len(raw))
	}

	samples := make([]int16, len(raw)/2)
	for i := range samples {
		samples[i] = int16(binary.LittleEndian.Uint16(raw[i*2:]))
	}
	return samples, nil
}
