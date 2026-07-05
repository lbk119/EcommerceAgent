package config

import (
	"net/url"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Addr             string
	Mode             string
	PythonBrainURL   *url.URL
	OutputDir        string
	JWTSecret        string
	JWTExpiresIn     time.Duration
	AuthEnabled      bool
	UserStoreBackend string
	CasbinModel      string
	CasbinPolicy     string
}

func Load() (Config, error) {
	brainURL, err := url.Parse(envOrDefault("PYTHON_BRAIN_URL", "http://127.0.0.1:8000"))
	if err != nil {
		return Config{}, err
	}

	return Config{
		Addr:             envOrDefault("GATEWAY_ADDR", ":9090"),
		Mode:             envOrDefault("GIN_MODE", "debug"),
		PythonBrainURL:   brainURL,
		OutputDir:        envOrDefault("OUTPUT_DIR", "output"),
		JWTSecret:        envOrDefault("GATEWAY_JWT_SECRET", "dev-only-change-me"),
		JWTExpiresIn:     time.Duration(envIntOrDefault("GATEWAY_JWT_EXPIRES_SECONDS", 7200)) * time.Second,
		AuthEnabled:      envBoolOrDefault("GATEWAY_AUTH_ENABLED", true),
		UserStoreBackend: envOrDefault("GATEWAY_USER_STORE_BACKEND", "static"),
		CasbinModel:      envOrDefault("GATEWAY_CASBIN_MODEL", "gateway/configs/casbin/model.conf"),
		CasbinPolicy:     envOrDefault("GATEWAY_CASBIN_POLICY", "gateway/configs/casbin/policy.csv"),
	}, nil
}

func envOrDefault(key string, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}

func envIntOrDefault(key string, fallback int) int {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func envBoolOrDefault(key string, fallback bool) bool {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return fallback
	}
	return parsed
}
