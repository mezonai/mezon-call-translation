// Package logging provides a process-wide structured logger.
package logging

import (
	"fmt"
	"log/slog"
	"os"
)

var L = newLogger(slog.LevelInfo)

// ConfigureFromEnv applies LOG_LEVEL after the application's .env file has
// been loaded. Invalid or empty values fall back to info.
func ConfigureFromEnv() {
	level := slog.LevelInfo
	if value := os.Getenv("LOG_LEVEL"); value != "" {
		_ = level.UnmarshalText([]byte(value))
	}
	L = newLogger(level)
}

func newLogger(level slog.Level) *slog.Logger {
	return slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
}

// ErrAttrs returns structured attributes for an error and its concrete type.
func ErrAttrs(err error) []any {
	if err == nil {
		return nil
	}
	return []any{"err", err.Error(), "err_type", fmt.Sprintf("%T", err)}
}
