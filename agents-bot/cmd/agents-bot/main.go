// Command agents-bot is the agents-bot service entrypoint. It initializes the
// mezon-sdk-go bot client, listens for voice/chat events from the Mezon
// platform, and serves an HTTP API for agents to resolve user profiles and
// register active meeting rooms.
package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/joho/godotenv"

	"github.com/mezonai/mezon-call-translation/agents-bot/internal/config"
	"github.com/mezonai/mezon-call-translation/agents-bot/internal/gateway"
)

func main() {
	// Best-effort .env loading for local development
	_ = godotenv.Load()

	cfg, err := config.FromEnv()
	if err != nil {
		log.Fatalf("agents-bot: config error: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	gw, err := gateway.New(cfg)
	if err != nil {
		log.Fatalf("agents-bot: init error: %v", err)
	}

	if err := gw.Run(ctx); err != nil {
		log.Printf("agents-bot: %v", err)
		os.Exit(1)
	}
}
