package gateway

import (
	"reflect"
	"testing"

	mezon "github.com/quangledang23/mezon-sdk-go"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/orchestratorclient"
)

func TestRecipientIDs(t *testing.T) {
	client, err := mezon.NewMezonClient(mezon.ClientConfig{BotID: "999", Token: "token"})
	if err != nil {
		t.Fatal(err)
	}
	client.ClientID = "999"
	g := &Gateway{client: client}

	got := g.recipientIDs([]orchestratorclient.Participant{
		{Identity: "123"},
		{Identity: "EG_guest"},
		{Identity: "999"},
		{Identity: " 456 "},
	})
	want := []string{"123", "456"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("recipientIDs() = %v, want %v", got, want)
	}
}

func TestFormatSummaryText(t *testing.T) {
	summary := &orchestratorclient.RoomSummary{
		RoomName: "Daily",
		SummaryData: orchestratorclient.MeetingSummary{
			Summary: "Deployment approved.",
			ActionItems: map[string][]string{
				"Tina": {"Deploy Friday"},
			},
		},
	}

	got := formatSummaryText(summary)
	want := "Meeting summary: Daily\n\nDeployment approved.\n\nAction items\n• Tina: Deploy Friday"
	if got != want {
		t.Fatalf("formatSummaryText() = %q, want %q", got, want)
	}
}
