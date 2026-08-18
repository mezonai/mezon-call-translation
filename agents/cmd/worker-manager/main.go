// Command worker-manager subscribes to the NATS start/stop events BE mezon
// publishes (mezon-sfu-migration-plan.md section 1) and spawns/kills one
// `agent` (cmd/agent) subprocess per room_id. See internal/workermanager's
// package doc for why this is its own binary rather than living inside
// orchestrator_service.
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/workermanager"
)

func main() {
	cfg, err := workermanager.FromEnv()
	if err != nil {
		logging.L.Error("workermanager: failed to load config", logging.ErrAttrs(err)...)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	m := workermanager.New(cfg)

	logging.L.Info("workermanager: starting", "agent_bin_path", cfg.AgentBinPath)
	if err := workermanager.RunSubscriber(ctx, cfg, m); err != nil {
		logging.L.Error("workermanager: exiting", logging.ErrAttrs(err)...)
		os.Exit(1)
	}
}
