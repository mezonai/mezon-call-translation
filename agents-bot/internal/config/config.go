// Package config loads the agents-bot runtime parameters from environment
// variables. The gateway runs as a standalone long-lived service (one
// instance per deployment), not a per-room subprocess like agent.
package config

import (
	"fmt"
	"os"
	"strconv"
)

type Config struct {
	// Mezon bot credentials
	BotID    string
	BotToken string

	// Mezon server connection
	MezonHost        string
	MezonPort        string
	MezonUseSSL      bool
	MezonTLSInsecure bool // skip TLS verify for dev self-signed certs

	// HTTP server
	GatewayPort int

	// Orchestrator connection (for forwarding chat)
	OrchestratorBaseURL string
	InternalAPISecret   string
}

func FromEnv() (Config, error) {
	cfg := Config{
		BotID:               requireEnv("MEZON_BOT_ID"),
		BotToken:            requireEnv("MEZON_BOT_TOKEN"),
		MezonHost:           getEnv("MEZON_HOST", ""),
		MezonPort:           getEnv("MEZON_PORT", ""),
		MezonTLSInsecure:    getEnv("MEZON_TLS_INSECURE", "") == "true",
		OrchestratorBaseURL: getEnv("ORCHESTRATOR_BASE_URL", "http://localhost:8002"),
		InternalAPISecret:   getEnv("INTERNAL_API_SECRET", ""),
	}

	if cfg.BotID == "" {
		return Config{}, fmt.Errorf("config: MEZON_BOT_ID is required")
	}
	if cfg.BotToken == "" {
		return Config{}, fmt.Errorf("config: MEZON_BOT_TOKEN is required")
	}

	sslStr := getEnv("MEZON_USE_SSL", "true")
	cfg.MezonUseSSL = sslStr != "false"

	port, err := strconv.Atoi(getEnv("GATEWAY_PORT", "8003"))
	if err != nil {
		return Config{}, fmt.Errorf("config: invalid GATEWAY_PORT: %w", err)
	}
	cfg.GatewayPort = port

	return cfg, nil
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func requireEnv(key string) string {
	return os.Getenv(key)
}
