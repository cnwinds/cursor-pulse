package main

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
)

type Config struct {
	Listen     string   `json:"listen"`
	Keys       []string `json:"keys,omitempty"` // optional local fallback; Pulse mode overrides via pool
	PulseURL   string   `json:"pulse_url"`
	PulseToken string   `json:"pulse_token"`
	PoolEvery  string   `json:"pool_every"` // e.g. "60s"
}

func loadConfig(path string) (*Config, error) {
	cfg := &Config{}
	b, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return cfg, nil
		}
		return nil, err
	}
	if err := json.Unmarshal(b, cfg); err != nil {
		return nil, err
	}
	return cfg, nil
}

func (c *Config) save(path string) error {
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o600)
}

func splitKeys(s string) []string {
	var out []string
	for _, k := range strings.Split(s, ",") {
		k = strings.TrimSpace(k)
		if k != "" {
			out = append(out, k)
		}
	}
	return out
}
