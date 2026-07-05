package main

import (
	"log/slog"
	"net/http"
	"os"
	"time"

	"DeepAgent/gateway/internal/auth"
	"DeepAgent/gateway/internal/authorization"
	"DeepAgent/gateway/internal/config"
	"DeepAgent/gateway/internal/handlers"
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

	userStore, err := auth.NewUserStoreFromConfig(cfg.UserStoreBackend)
	if err != nil {
		slog.Error("initialize gateway user store", "backend", cfg.UserStoreBackend, "error", err)
		os.Exit(1)
	}
	tokenManager, err := auth.NewTokenManager(cfg.JWTSecret, cfg.JWTExpiresIn)
	if err != nil {
		slog.Error("initialize gateway auth", "error", err)
		os.Exit(1)
	}
	enforcer, err := authorization.NewEnforcer(cfg.CasbinModel, cfg.CasbinPolicy)
	if err != nil {
		slog.Error("initialize casbin authorization", "error", err)
		os.Exit(1)
	}
	authHandler := handlers.NewAuthHandler(userStore, tokenManager, enforcer)

	brainProxy := proxy.NewBrainProxy(cfg.PythonBrainURL)
	engine := router.New(cfg, brainProxy, authHandler, tokenManager, userStore, enforcer)

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
