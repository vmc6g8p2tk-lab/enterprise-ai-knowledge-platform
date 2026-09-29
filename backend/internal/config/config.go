package config

import (
	"errors"
	"os"
)

type Config struct {
	DatabaseURL string
	AIServiceURL string
	UploadDir string
	HTTPAddr string
	CORSOrigin string
}

func Load() (Config, error) {
	c := Config{
		DatabaseURL: os.Getenv("DATABASE_URL"),
		AIServiceURL: env("AI_SERVICE_URL", "http://localhost:8000"),
		UploadDir: env("UPLOAD_DIR", "./uploads"),
		HTTPAddr: env("HTTP_ADDR", ":8080"),
		CORSOrigin: env("CORS_ORIGIN", "http://localhost:5173"),
	}
	if c.DatabaseURL == "" { return c, errors.New("DATABASE_URL is required") }
	return c, nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" { return value }
	return fallback
}
