// Package logging provides a process-wide structured logger.
package logging

import (
	"fmt"
	"log/slog"
	"os"
)

var L *slog.Logger

func init() {
	level := slog.LevelInfo
	if v := os.Getenv("LOG_LEVEL"); v != "" {
		_ = level.UnmarshalText([]byte(v))
	}
	L = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
}

// ErrAttrs returns log attrs carrying both the error message and its Go type,
// since some error values (e.g. context.DeadlineExceeded-wrapped errors) stringify
// uninformatively on their own.
func ErrAttrs(err error) []any {
	if err == nil {
		return nil
	}
	return []any{"err", err.Error(), "err_type", errType(err)}
}

func errType(err error) string {
	return fmt.Sprintf("%T", err)
}
