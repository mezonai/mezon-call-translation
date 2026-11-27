package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	mezon "github.com/quangledang23/mezon-sdk-go"
	"github.com/quangledang23/mezon-sdk-go/rtapi"
	"google.golang.org/protobuf/proto"
)

// sendSummaryDM resolves the participant's Mezon DM channel and sends the
// completed meeting summary as a system message.
func (g *Gateway) sendSummaryDM(
	ctx context.Context,
	participantID string,
	summaryText string,
) (*rtapi.ChannelMessageAck, error) {
	if !mezon.IsValidUserID(participantID) {
		return nil, fmt.Errorf("send summary DM: invalid participant ID %q", participantID)
	}
	if strings.TrimSpace(summaryText) == "" {
		return nil, fmt.Errorf("send summary DM: summary text is empty")
	}
	if g.client == nil || g.client.ChannelManager() == nil {
		return nil, fmt.Errorf("send summary DM: Mezon client is unavailable")
	}

	dmChannel, err := g.client.ChannelManager().CreateDMChannel(participantID)
	if err != nil {
		return nil, fmt.Errorf("send summary DM: create DM channel for %s: %w", participantID, err)
	}
	if dmChannel == nil || dmChannel.ChannelId == 0 {
		return nil, fmt.Errorf("send summary DM: no DM channel returned for %s", participantID)
	}

	// The SDK keeps the current authenticated session on pseudo-clan "0".
	// Reading it at send time uses the latest token after an SDK refresh.
	sessionClan, ok := g.client.Clans.Get("0")
	if !ok || sessionClan == nil || sessionClan.SessionToken == "" {
		return nil, fmt.Errorf("send summary DM: current Mezon session token is unavailable")
	}

	content, err := json.Marshal(map[string]string{"t": summaryText})
	if err != nil {
		return nil, fmt.Errorf("send summary DM: encode message content: %w", err)
	}

	return g.sendSystemMessage(ctx, sessionClan.SessionToken, &rtapi.ChannelMessageSend{
		ClanId:    dmChannel.ClanId,
		ChannelId: dmChannel.ChannelId,
		Content:   string(content),
		Mode:      int32(mezon.StreamModeDM),
		IsPublic:  false,
		Code:      0,
	})
}

// sendSystemMessage is the Go equivalent of the Mezon team's TypeScript
// fetch(Mezon/SendSystemMessage) helper. This API has no socket api_index, so
// it must be called directly over HTTP with protobuf request/response bodies.
func (g *Gateway) sendSystemMessage(
	ctx context.Context,
	token string,
	message *rtapi.ChannelMessageSend,
) (*rtapi.ChannelMessageAck, error) {
	if strings.TrimSpace(token) == "" {
		return nil, fmt.Errorf("send system message: bearer token is empty")
	}
	if message == nil {
		return nil, fmt.Errorf("send system message: message is nil")
	}
	if g.systemHTTP == nil {
		return nil, fmt.Errorf("send system message: HTTP client is unavailable")
	}

	body, err := proto.Marshal(message)
	if err != nil {
		return nil, fmt.Errorf("send system message: encode request: %w", err)
	}

	endpoint := strings.TrimRight(g.cfg.MezonSystemAPIURL, "/") +
		"/mezon.api.Mezon/SendSystemMessage"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("send system message: build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/proto")
	req.Header.Set("Accept", "application/proto")

	resp, err := g.systemHTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("send system message: HTTP request: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	responseBody, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		return nil, fmt.Errorf("send system message: read response: %w", err)
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("send system message: HTTP %d", resp.StatusCode)
	}

	ack := &rtapi.ChannelMessageAck{}
	if err := proto.Unmarshal(responseBody, ack); err != nil {
		return nil, fmt.Errorf("send system message: decode acknowledgement: %w", err)
	}
	return ack, nil
}
