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
