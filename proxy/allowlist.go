package main

import (
	"net"
	"os"
	"strings"
)

const defaultConnectAllowlist = "*.cursor.sh,cursor.sh"

func parseConnectAllowlist(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		raw = defaultConnectAllowlist
	}
	var out []string
	for _, p := range strings.Split(raw, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	if len(out) == 0 {
		return []string{"*.cursor.sh", "cursor.sh"}
	}
	return out
}

func resolveConnectAllowlist() []string {
	return parseConnectAllowlist(strings.TrimSpace(os.Getenv("PROXY_CONNECT_ALLOWLIST")))
}

func hostFromAuthority(authority string) string {
	host := authority
	if h, _, err := net.SplitHostPort(authority); err == nil {
		host = h
	}
	return strings.ToLower(host)
}

func hostAllowed(authority string, patterns []string) bool {
	host := hostFromAuthority(authority)
	for _, pattern := range patterns {
		if matchHostPattern(host, pattern) {
			return true
		}
	}
	return false
}

func matchHostPattern(host, pattern string) bool {
	pattern = strings.ToLower(strings.TrimSpace(pattern))
	if pattern == "" {
		return false
	}
	if pattern == "*" {
		return true
	}
	if strings.HasPrefix(pattern, "*.") {
		suffix := pattern[1:] // ".cursor.sh"
		apex := pattern[2:]  // "cursor.sh"
		return host == apex || strings.HasSuffix(host, suffix)
	}
	return host == pattern
}
