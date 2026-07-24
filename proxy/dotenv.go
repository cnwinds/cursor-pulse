package main

import (
	"bufio"
	"os"
	"strings"
)

// dockerEnvPathForTest overrides the Docker mount path in unit tests.
var dockerEnvPathForTest = ""

func dockerMountedEnvPath() string {
	if dockerEnvPathForTest != "" {
		return dockerEnvPathForTest
	}
	return "/app/.env"
}

// bootstrapDockerDotenv reloads Docker-mounted docker/.env.
//
// Compose env_file only injects at container create time, so a plain restart
// would keep stale values unless we re-read the mounted file with override.
//
// Main-stack .env sets PULSE_BASE_URL=http://web:8080, which the proxy network
// cannot resolve. Prefer PROXY_PULSE_BASE_URL when present; otherwise keep the
// compose-injected PULSE_BASE_URL (host.docker.internal by default).
func bootstrapDockerDotenv() {
	composePulseURL := os.Getenv("PULSE_BASE_URL")
	if err := loadEnvFile(dockerMountedEnvPath(), true); err != nil {
		return
	}
	if v := strings.TrimSpace(os.Getenv("PROXY_PULSE_BASE_URL")); v != "" {
		_ = os.Setenv("PULSE_BASE_URL", v)
		return
	}
	if composePulseURL != "" {
		_ = os.Setenv("PULSE_BASE_URL", composePulseURL)
	}
}

func loadEnvFile(path string, override bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "export ") {
			line = strings.TrimSpace(strings.TrimPrefix(line, "export "))
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		if key == "" {
			continue
		}
		if !override {
			if _, exists := os.LookupEnv(key); exists {
				continue
			}
		}
		_ = os.Setenv(key, unquoteEnvValue(strings.TrimSpace(val)))
	}
	return sc.Err()
}

func unquoteEnvValue(v string) string {
	if len(v) >= 2 {
		if (v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'') {
			return v[1 : len(v)-1]
		}
	}
	if i := strings.Index(v, " #"); i >= 0 {
		return strings.TrimSpace(v[:i])
	}
	return v
}
