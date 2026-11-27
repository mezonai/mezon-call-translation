package gateway

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/quangledang23/mezon-sdk-go/rtapi"
	"google.golang.org/protobuf/proto"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
)

func TestSendSystemMessageProtobufHTTP(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mezon.api.Mezon/SendSystemMessage" {
			t.Fatalf("path = %q", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer session-token" {
			t.Fatalf("authorization = %q", got)
		}
		body := &rtapi.ChannelMessageSend{}
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatal(err)
		}
		if err := proto.Unmarshal(raw, body); err != nil {
			t.Fatal(err)
		}
		if body.ChannelId != 456 || body.Mode != 4 {
			t.Fatalf("message = %+v", body)
		}

		response, err := proto.Marshal(&rtapi.ChannelMessageAck{ChannelId: 456, MessageId: 789})
		if err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/proto")
		_, _ = w.Write(response)
	}))
	defer server.Close()

	g := &Gateway{
		cfg:        config.Config{MezonSystemAPIURL: server.URL},
		systemHTTP: server.Client(),
	}
	ack, err := g.sendSystemMessage(context.Background(), "session-token", &rtapi.ChannelMessageSend{
		ClanId:    0,
		ChannelId: 456,
		Content:   `{"t":"summary"}`,
		Mode:      4,
	})
	if err != nil {
		t.Fatal(err)
	}
	if ack.GetMessageId() != 789 {
		t.Fatalf("message id = %d", ack.GetMessageId())
	}
}
