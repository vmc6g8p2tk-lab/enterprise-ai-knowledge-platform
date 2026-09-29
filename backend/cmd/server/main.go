package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/config"
	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/httpapi"
	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/migrate"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "check local HTTP readiness")
	flag.Parse()
	if *healthcheck {
		client := http.Client{Timeout: 2 * time.Second}
		resp, err := client.Get("http://localhost:8080/healthz")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = resp.Body.Close()
		return
	}
	cfg, err := config.Load()
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		log.Fatal(err)
	}
	if err := migrate.Apply(ctx, pool, "migrations"); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(cfg.UploadDir, 0700); err != nil {
		log.Fatal(err)
	}
	server := &http.Server{Addr: cfg.HTTPAddr, Handler: httpapi.New(pool, cfg), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer stopCancel()
		_ = server.Shutdown(stopCtx)
	}()
	log.Printf("Go API listening on %s", cfg.HTTPAddr)
	if err := server.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
