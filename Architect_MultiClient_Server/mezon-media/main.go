

package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const httpTimeout = 15 * time.Second


func main() {
	port := flag.Int("port", 8081, "listen on this port")
	debug := flag.Bool("debug", false, "enable debug log")
	flag.Parse()

	var programLevel = new(slog.LevelVar) // Info by default
	f, err := os.OpenFile("./stn.log", os.O_RDWR|os.O_CREATE|os.O_APPEND, 0666)
	if err != nil {
		panic("can not create log file")
	}
	defer f.Close()
	wrt := io.MultiWriter(os.Stdout, f)
	logger := slog.New(slog.NewTextHandler(wrt, &slog.HandlerOptions{Level: programLevel}))
	slog.SetDefault(logger)

	if *debug {
		programLevel.Set(slog.LevelDebug)
	}



	slog.Info("starting server")
	http.HandleFunc("/api/create_dispatcher", createDispatch)
	http.HandleFunc("/api/cancel_dispatcher", cancelDispatch)
	slog.Info("listening on port", "port", *port)

	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", *port),
		WriteTimeout: httpTimeout,
		ReadTimeout:  httpTimeout,
	}
	go func() {
		err := srv.ListenAndServe()
		if err != nil && err != http.ErrServerClosed {
			slog.Error("error starting server", "err", err)
		}
	}()

	// trap sigterm or interrupt and gracefully shutdown the server
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT)
	signal.Notify(sigChan, syscall.SIGTERM)

	// block until a signal is received
	sig := <-sigChan
	slog.Info("got signal", "sig", sig)
	slog.Info("shutting down")

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		slog.Error("graceful shutdown failed", "err", err)
		os.Exit(1)
	}
}
