package workermanager

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/nats-io/nats.go"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
)

// RunSubscriber connects to NATS, subscribes to the start/stop subjects
// (queue-grouped so that if this manager is ever scaled to N instances,
// only one of them handles a given event -- see the Config doc), and blocks
// until ctx is cancelled.
func RunSubscriber(ctx context.Context, cfg Config, m *Manager) error {
	nc, err := nats.Connect(cfg.NATSURL)
	if err != nil {
		return fmt.Errorf("workermanager: connect nats %s: %w", cfg.NATSURL, err)
	}
	defer nc.Close()

	startSub, err := nc.QueueSubscribe(cfg.StartSubject, cfg.QueueGroup, func(msg *nats.Msg) {
		var ev StartEvent
		if err := json.Unmarshal(msg.Data, &ev); err != nil {
			logging.L.Warn("workermanager: undecodable start event", logging.ErrAttrs(err)...)
			return
		}
		if err := m.Start(ev); err != nil {
			logging.L.Error("workermanager: start failed", append(logging.ErrAttrs(err), "room_id", ev.RoomID)...)
		}
	})
	if err != nil {
		return fmt.Errorf("workermanager: subscribe %s: %w", cfg.StartSubject, err)
	}
	defer func() { _ = startSub.Unsubscribe() }()

	stopSub, err := nc.QueueSubscribe(cfg.StopSubject, cfg.QueueGroup, func(msg *nats.Msg) {
		var ev StopEvent
		if err := json.Unmarshal(msg.Data, &ev); err != nil {
			logging.L.Warn("workermanager: undecodable stop event", logging.ErrAttrs(err)...)
			return
		}
		if err := m.Stop(ev); err != nil {
			logging.L.Error("workermanager: stop failed", append(logging.ErrAttrs(err), "room_id", ev.RoomID)...)
		}
	})
	if err != nil {
		return fmt.Errorf("workermanager: subscribe %s: %w", cfg.StopSubject, err)
	}
	defer func() { _ = stopSub.Unsubscribe() }()

	logging.L.Info("workermanager: subscribed",
		"nats_url", cfg.NATSURL, "start_subject", cfg.StartSubject, "stop_subject", cfg.StopSubject, "queue_group", cfg.QueueGroup)

	<-ctx.Done()
	m.Shutdown()
	return nil
}
