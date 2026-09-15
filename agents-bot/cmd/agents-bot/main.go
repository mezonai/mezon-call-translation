// Command agents-bot is the agents-bot service entrypoint. It initializes the
// mezon-sdk-go bot client, listens for voice/chat events from the Mezon
// platform, and serves an HTTP API for agents to resolve user profiles and
// register active meeting rooms.
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/joho/godotenv"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/gateway"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/logging"
)

func main() {
	// Best-effort .env loading for local development
	_ = godotenv.Load()
	logging.ConfigureFromEnv()

	cfg, err := config.FromEnv()
	if err != nil {
		logging.L.Error("agents-bot: config error", logging.ErrAttrs(err)...)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	gw, err := gateway.New(cfg)
	if err != nil {
		logging.L.Error("agents-bot: init error", logging.ErrAttrs(err)...)
		os.Exit(1)
	}

	if err := gw.Run(ctx); err != nil {
		logging.L.Error("agents-bot: stopped with error", logging.ErrAttrs(err)...)
		os.Exit(1)
	}
}
