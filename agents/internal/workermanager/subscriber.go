package workermanager

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/nats-io/nats.go"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// RunSubscriber connects to NATS, subscribes to cfg.Subject (queue-grouped
// so that if this manager is ever scaled to N instances, only one of them
// handles a given event -- see the Config doc), and blocks until ctx is
// cancelled.
//
// cfg.Subject is shared with mezon-sfu's own participant hook events (see
// dispatchEvent's doc) -- most messages received here will NOT be an
// add/delete dispatch, and that's expected, not an error: anything that
// doesn't decode into a recognized "action" is logged at debug level and
// dropped.
//
// handleDispatch itself stays cheap and never blocks on Start/Stop directly:
// it routes the actual work through Manager.dispatch (see shard.go), since
// nats.go invokes this callback from one goroutine per subscription,
// in order -- calling Start/Stop synchronously here would mean one room's
// slow Stop blocks every other room's events until it's done.
func RunSubscriber(ctx context.Context, cfg Config, m *Manager) error {
	nc, err := nats.Connect(cfg.NATSURL)
	if err != nil {
		return fmt.Errorf("workermanager: connect nats %s: %w", cfg.NATSURL, err)
	}
	defer nc.Close()

	sub, err := nc.QueueSubscribe(cfg.Subject, cfg.QueueGroup, func(msg *nats.Msg) {
		handleDispatch(msg.Data, m)
	})
	if err != nil {
		return fmt.Errorf("workermanager: subscribe %s: %w", cfg.Subject, err)
	}
	defer func() { _ = sub.Unsubscribe() }()

	logging.L.Info("workermanager: subscribed",
		"nats_url", cfg.NATSURL, "subject", cfg.Subject, "queue_group", cfg.QueueGroup)

	<-ctx.Done()
	m.Shutdown()
	return nil
}

func handleDispatch(data []byte, m *Manager) {
	var ev dispatchEvent
	if err := json.Unmarshal(data, &ev); err != nil {
		logging.L.Debug("workermanager: undecodable message on dispatch subject, ignoring", logging.ErrAttrs(err)...)
		return
	}
	if ev.Action == "" {
		// Almost certainly one of mezon-sfu's own hook events
		// ({"event":...}) sharing this subject, not a dispatch message.
		return
	}

	roomID, err := strconv.ParseUint(ev.RoomID, 10, 64)
	if err != nil {
		logging.L.Warn("workermanager: dispatch event with unparseable room_id",
			append(logging.ErrAttrs(err), "action", ev.Action, "room_id_raw", ev.RoomID)...)
		return
	}

	switch ev.Action {
	case "add":
		m.dispatch(roomID, func() {
			if err := m.Start(StartEvent{RoomID: roomID}); err != nil {
				logging.L.Error("workermanager: start failed", append(logging.ErrAttrs(err), "room_id", roomID)...)
			}
		})
	case "delete":
		m.dispatch(roomID, func() {
			if err := m.Stop(StopEvent{RoomID: roomID}); err != nil {
				logging.L.Error("workermanager: stop failed", append(logging.ErrAttrs(err), "room_id", roomID)...)
			}
		})
	default:
		logging.L.Debug("workermanager: dispatch event with unrecognized action, ignoring", "action", ev.Action, "room_id", roomID)
	}
}
