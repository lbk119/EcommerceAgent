package config

import (
	"net/url"
	"os"
)

type Config struct {
	Addr           string
	Mode           string
	PythonBrainURL *url.URL
	OutputDir      string
}

func Load() (Config, error) {
	brainURL, err := url.Parse(envOrDefault("PYTHON_BRAIN_URL", "http://127.0.0.1:8000"))
	if err != nil {
		return Config{}, err
	}

	return Config{
		Addr:           envOrDefault("GATEWAY_ADDR", ":9090"),
		Mode:           envOrDefault("GIN_MODE", "debug"),
		PythonBrainURL: brainURL,
		OutputDir:      envOrDefault("OUTPUT_DIR", "output"),
	}, nil
}

func envOrDefault(key string, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}