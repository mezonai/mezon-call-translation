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

	"github.com/joho/godotenv"

	"github.com/mezonai/mezon-call-translation/agents/internal/logging"
	"github.com/mezonai/mezon-call-translation/agents/internal/workermanager"
)

func main() {
	// Best-effort: a missing .env (the common case outside local dev) isn't
	// an error, everything still falls back to the shell's real env/config
	// defaults exactly as before this existed.
	_ = godotenv.Load()

	cfg, err := workermanager.FromEnv()
	if err != nil {
		logging.L.Error("workermanager: failed to load config", logging.ErrAttrs(err)...)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	m := workermanager.New(cfg)

	// Re-adopt agents a previous instance of this worker-manager left
	// running (crash, redeploy, manual restart) -- Phase 3 of
	// mezon-sfu-migration-plan.md's restart-recovery gap. Must happen
	// before subscribing to NATS below, so no Start/Stop for a room this is
	// about to adopt can race the reconciliation itself.
	m.Reconcile()

	logging.L.Info("workermanager: starting", "agent_bin_path", cfg.AgentBinPath)
	if err := workermanager.RunSubscriber(ctx, cfg, m); err != nil {
		logging.L.Error("workermanager: exiting", logging.ErrAttrs(err)...)
		os.Exit(1)
	}
}
