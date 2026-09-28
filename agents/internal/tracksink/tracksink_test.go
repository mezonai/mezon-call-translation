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
