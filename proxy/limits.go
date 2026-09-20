package main

import (
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultMaxRequestBody   = 32 << 20 // 32 MiB
	defaultMaxUsageTapBuffer = 8 << 20  // 8 MiB
	defaultReadHeaderTimeout = 30 * time.Second
	defaultIdleTimeout       = 120 * time.Second
	defaultExhaustedReset = 30 * time.Minute // override with PROXY_EXHAUSTED_RESET; 0/off/false disables
)

// maxUsageTapBuffer caps usageTapWriter accumulation; tests may temporarily lower it.
var maxUsageTapBuffer = defaultMaxUsageTapBuffer

func maxNonStreamBodyLimit() int64 {
	if raw := strings.TrimSpace(os.Getenv("PROXY_MAX_BODY")); raw != "" {
		if n, err := strconv.ParseInt(raw, 10, 64); err == nil && n > 0 {
			return n
		}
	}
	return defaultMaxRequestBody
}

func resolveExhaustedResetInterval() time.Duration {
	raw := strings.TrimSpace(os.Getenv("PROXY_EXHAUSTED_RESET"))
	if raw == "0" || strings.EqualFold(raw, "off") || strings.EqualFold(raw, "false") {
		return 0
	}
	if raw == "" {
		return defaultExhaustedReset
	}
	if d, err := time.ParseDuration(raw); err == nil && d > 0 {
		return d
	}
	return defaultExhaustedReset
}

// parseDurationValue accepts a bare integer (interpreted as seconds) or a Go
// duration string. ok=false when the value is neither.
//
// Shared by the resolvers that accept the "bare seconds" convention
// (resolveStickyMinDwell / resolveSessionTTL). resolveExhaustedResetInterval
// deliberately keeps its own ParseDuration-only parsing: it predates this
// helper and accepting bare seconds there would silently change
// PROXY_EXHAUSTED_RESET=30 from "invalid → default 30m" to "30s".
func parseDurationValue(raw string) (time.Duration, bool) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return 0, false
	}
	if secs, err := strconv.Atoi(text); err == nil {
		return time.Duration(secs) * time.Second, true
	}
	if d, err := time.ParseDuration(text); err == nil {
		return d, true
	}
	return 0, false
}
