package main

import (
	"log/slog"
	"net/http"
	"os"
	"time"

	"DeepAgent/gateway/internal/config"
	"DeepAgent/gateway/internal/proxy"
	"DeepAgent/gateway/internal/router"
	"github.com/gin-gonic/gin"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		slog.Error("load gateway config", "error", err)
		os.Exit(1)
	}

	if cfg.Mode != "" {
		gin.SetMode(cfg.Mode)
	}

	brainProxy := proxy.NewBrainProxy(cfg.PythonBrainURL)
	engine := router.New(cfg, brainProxy)

	server := &http.Server{
		Addr:              cfg.Addr,
		Handler:           engine,
		ReadHeaderTimeout: 10 * time.Second,
	}

	slog.Info("gateway started", "addr", cfg.Addr, "python_brain_url", cfg.PythonBrainURL.String())
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("gateway stopped", "error", err)
		os.Exit(1)
	}
}