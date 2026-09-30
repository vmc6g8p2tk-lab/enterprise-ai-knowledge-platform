package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
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
	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/jobs"
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
	defer func() { cancel(); pool.Close() }()
	if err := pool.Ping(ctx); err != nil {
		log.Fatal(err)
	}
	if err := migrate.Apply(ctx, pool, "migrations"); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(cfg.UploadDir, 0700); err != nil {
		log.Fatal(err)
	}
	for range cfg.WorkerConcurrency {
		var idBytes [16]byte
		if _, err := rand.Read(idBytes[:]); err != nil {
			log.Fatal(err)
		}
		worker := &jobs.Worker{DB: pool, AIURL: cfg.AIServiceURL, Token: cfg.InternalToken,
			ID: hex.EncodeToString(idBytes[:]), HTTPClient: &http.Client{Timeout: 15 * time.Minute}}
		go worker.Run(ctx)
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
