// Package reconnect implements bounded exponential backoff for the agent's
// session retry loop. See config.ReconnectConfig for why this exists and
// what it is not a substitute for (process-level supervision).
package reconnect

import (
	"math/rand"
	"time"

	"github.com/mezonai/mezon-call-translation/agents/internal/config"
)

type Backoff struct {
	cfg     config.ReconnectConfig
	attempt int
}

func New(cfg config.ReconnectConfig) *Backoff {
	return &Backoff{cfg: cfg}
}

// Reset clears the attempt counter, e.g. after a session stayed up long
// enough to be considered healthy again.
func (b *Backoff) Reset() {
	b.attempt = 0
}

// Attempts returns how many consecutive failed attempts have been counted
// since the last Reset.
func (b *Backoff) Attempts() int {
	return b.attempt
}

// Next reports whether another retry is allowed and, if so, how long to
// wait first. ok is false once cfg.MaxAttempts consecutive failures have
// been recorded (or immediately, if MaxAttempts is 0).
func (b *Backoff) Next() (delay time.Duration, ok bool) {
	if b.attempt >= b.cfg.MaxAttempts {
		return 0, false
	}
	b.attempt++

	// delay = min(BaseDelay * 2^(attempt-1), MaxDelay), plus up to 20% jitter
	// so a fleet of agents reconnecting after a shared blip doesn't all hit
	// mezon-sfu in lockstep.
	d := b.cfg.BaseDelay << (b.attempt - 1)
	if d <= 0 || d > b.cfg.MaxDelay { // overflow or past cap
		d = b.cfg.MaxDelay
	}
	jitter := time.Duration(rand.Int63n(int64(d)/5 + 1))
	return d + jitter, true
}
