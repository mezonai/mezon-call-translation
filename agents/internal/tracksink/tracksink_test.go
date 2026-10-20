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

func TestRecordTrackIDDiffersAcrossJoinedEpochs(t *testing.T) {
	first := RecordTrackID(3, rtcagent.KindAudio, 1, 1)
	second := RecordTrackID(3, rtcagent.KindAudio, 1, 2)
	third := RecordTrackID(3, rtcagent.KindAudio, 1, 3)
	if first != RecordTrackID(3, rtcagent.KindAudio, 1) {
		t.Fatal("first joined must retain the existing recording ID format")
	}
	if first == second || second == third || first == third {
		t.Fatal("a new joined on the same connection must not reuse recording IDs")
	}
}
