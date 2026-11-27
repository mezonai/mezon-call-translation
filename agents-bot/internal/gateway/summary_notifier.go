package gateway

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	mezon "github.com/quangledang23/mezon-sdk-go"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/orchestratorclient"
)

const roomSummaryDoneEvent = "room_summary_done"

// runSummaryNotifier owns the long-lived metadata SSE subscription. A fresh
// orchestrator JWT is obtained on every reconnect, which also recovers from
// access-token expiry without persisting credentials locally.
func (g *Gateway) runSummaryNotifier(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		// Authenticate with orchestrator. This token is used only for orchestrator endpoints:
			// - Metadata SSE
			// - Summary retrieval
			// - Participant retrieval
		session, err := g.summaryOrch.AuthenticateBot(ctx, g.cfg.BotID, g.cfg.BotToken)
		if err == nil {
			logging.L.Info("agents-bot: authenticated for summary metadata SSE")
			// This opens a long-running connection to: GET /api/v2/sse/metadata
			err = g.summaryOrch.StreamMetadata(
				ctx,
				session.AccessToken,
				func(event orchestratorclient.MetadataEvent) error {
					if event.EventType != roomSummaryDoneEvent {
						return nil
					}
					if event.Room.RoomID == "" {
						logging.L.Warn("agents-bot: room_summary_done event missing room_id", "event_id", event.EventID)
						return nil
					}

					handleErr := g.handleRoomSummaryDone(ctx, session.AccessToken, event)
					if errors.Is(handleErr, orchestratorclient.ErrUnauthorized) {
						return handleErr
					}
					if handleErr != nil {
						logging.L.Error(
							"agents-bot: process room_summary_done failed",
							append(logging.ErrAttrs(handleErr), "room_id", event.Room.RoomID, "event_id", event.EventID)...,
						)
					}
					return nil
				},
			)
		}

		if ctx.Err() != nil {
			return
		}
		logging.L.Warn("agents-bot: summary metadata SSE disconnected", append(logging.ErrAttrs(err), "retry_in", backoff)...)

		// Reconnect after disconnection
		timer := time.NewTimer(backoff)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		if backoff < 30*time.Second {
			backoff *= 2
			if backoff > 30*time.Second {
				backoff = 30 * time.Second
			}
		}
	}
}

func (g *Gateway) handleRoomSummaryDone(
	ctx context.Context,
	orchestratorToken string,
	event orchestratorclient.MetadataEvent,
) error {
	summary, err := g.summaryOrch.GetRoomSummary(ctx, orchestratorToken, event.Room.RoomID)
	if err != nil {
		return err
	}
	if strings.TrimSpace(summary.RoomName) == "" {
		summary.RoomName = event.Room.RoomName
	}
	participants, err := g.summaryOrch.GetRoomParticipants(ctx, orchestratorToken, event.Room.RoomID)
	if err != nil {
		return err
	}

	messageText := formatSummaryText(summary)
	if messageText == "" {
		return fmt.Errorf("completed summary contains no message text")
	}
	recipients := g.recipientIDs(participants)
	if len(recipients) == 0 {
		logging.L.Warn("agents-bot: completed room has no eligible DM recipients", "room_id", event.Room.RoomID)
		return nil
	}

	for _, participantID := range recipients {
		sendCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		ack, sendErr := g.sendSummaryDM(sendCtx, participantID, messageText)
		cancel()
		if sendErr != nil {
			// Delivery is independent per participant: one invalid/unreachable DM
			// must not prevent the remaining attendees from receiving a summary.
			logging.L.Error(
				"agents-bot: send summary DM failed",
				append(logging.ErrAttrs(sendErr), "room_id", event.Room.RoomID, "participant_id", participantID)...,
			)
			continue
		}
		logging.L.Info(
			"agents-bot: summary DM sent",
			"room_id", event.Room.RoomID,
			"participant_id", participantID,
			"channel_id", ack.GetChannelId(),
			"message_id", ack.GetMessageId(),
		)
	}
	return nil
}

// recipientIDs uses the orchestrator room roster as the source of truth.
// External/guest identities cannot form a Mezon DM, and the SFU roster may
// contain this agent itself, so those two cases are excluded.
func (g *Gateway) recipientIDs(participants []orchestratorclient.Participant) []string {
	result := make([]string, 0, len(participants))
	for _, participant := range participants {
		participantID := strings.TrimSpace(participant.Identity)
		if !mezon.IsValidUserID(participantID) || participantID == g.client.ClientID {
			continue
		}
		result = append(result, participantID)
	}
	return result
}

func formatSummaryText(summary *orchestratorclient.RoomSummary) string {
	if summary == nil {
		return ""
	}

	parts := make([]string, 0, 3)
	title := "Meeting summary"
	if roomName := strings.TrimSpace(summary.RoomName); roomName != "" && roomName != "Unknown" {
		title += ": " + roomName
	}
	parts = append(parts, title)
	if text := strings.TrimSpace(summary.SummaryData.Summary); text != "" {
		parts = append(parts, text)
	}

	owners := make([]string, 0, len(summary.SummaryData.ActionItems))
	for owner := range summary.SummaryData.ActionItems {
		owners = append(owners, owner)
	}
	sort.Strings(owners)
	actions := make([]string, 0)
	for _, owner := range owners {
		for _, task := range summary.SummaryData.ActionItems[owner] {
			if task = strings.TrimSpace(task); task != "" {
				actions = append(actions, fmt.Sprintf("• %s: %s", owner, task))
			}
		}
	}
	if len(actions) > 0 {
		parts = append(parts, "Action items\n"+strings.Join(actions, "\n"))
	}

	// A title alone is not a useful notification.
	if len(parts) == 1 {
		return ""
	}
	return strings.Join(parts, "\n\n")
}
