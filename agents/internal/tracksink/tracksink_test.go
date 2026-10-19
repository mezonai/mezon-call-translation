package tracksink

import (
	"testing"

	"github.com/mezonai/mezon-call-translation/agents/internal/rtcagent"
)

func TestRecordTrackIDDiffersAcrossConnections(t *testing.T) {
	first := RecordTrackID(3, rtcagent.KindAudio, 1)
	second := RecordTrackID(3, rtcagent.KindAudio, 2)
	if first == second {
		t.Fatalf("track id must differ across reconnects, both %q", first)
	}
	if first != "peer3-mic-c1" {
		t.Errorf("first = %q, want peer3-mic-c1", first)
	}
}

// The first joined keeps the historical ID; an in-place rejoin on the same
// socket must not reuse (and overwrite) that recording's track row.
func TestRecordTrackIDDiffersAcrossInPlaceRejoin(t *testing.T) {
	if got := RecordTrackID(3, rtcagent.KindAudio, 1, 1); got != "peer3-mic-c1" {
		t.Errorf("first joined = %q, want peer3-mic-c1", got)
	}
	if got := RecordTrackID(3, rtcagent.KindAudio, 1, 2); got != "peer3-mic-c1-e2" {
		t.Errorf("second joined = %q, want peer3-mic-c1-e2", got)
	}
}
