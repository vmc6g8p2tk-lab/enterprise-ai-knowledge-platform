package config

import (
	"errors"
	"os"
)

type Config struct {
	DatabaseURL       string
	AIServiceURL      string
	UploadDir         string
	HTTPAddr          string
	CORSOrigin        string
	JWTSecret         string
	InternalToken     string
	WorkerConcurrency int
}

func Load() (Config, error) {
	c := Config{
		DatabaseURL:       os.Getenv("DATABASE_URL"),
		AIServiceURL:      env("AI_SERVICE_URL", "http://localhost:8000"),
		UploadDir:         env("UPLOAD_DIR", "./uploads"),
		HTTPAddr:          env("HTTP_ADDR", ":8080"),
		CORSOrigin:        env("CORS_ORIGIN", "http://localhost:5173"),
		JWTSecret:         os.Getenv("JWT_SECRET"),
		InternalToken:     os.Getenv("INTERNAL_SERVICE_TOKEN"),
		WorkerConcurrency: 2,
	}
	if c.DatabaseURL == "" {
		return c, errors.New("DATABASE_URL is required")
	}
	if len(c.JWTSecret) < 32 || len(c.InternalToken) < 32 {
		return c, errors.New("JWT_SECRET and INTERNAL_SERVICE_TOKEN must each contain at least 32 bytes")
	}
	return c, nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
