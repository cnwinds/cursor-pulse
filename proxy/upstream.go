package main

import (
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

// parseUpstreamProxy parses PROXY_UPSTREAM_URL.
// Empty string means direct (no proxy). Supports http/https and userinfo auth.
func parseUpstreamProxy(raw string) (*url.URL, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("PROXY_UPSTREAM_URL: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, fmt.Errorf("PROXY_UPSTREAM_URL: unsupported scheme %q (want http or https)", u.Scheme)
	}
	if u.Host == "" {
		return nil, fmt.Errorf("PROXY_UPSTREAM_URL: missing host")
	}
	return u, nil
}

// redactUpstreamProxy masks userinfo for logs.
func redactUpstreamProxy(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "(direct)"
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "(invalid)"
	}
	if u.User != nil {
		return u.Scheme + "://***@" + u.Host
	}
	return u.Scheme + "://" + u.Host
}

// newOutboundTransport builds an HTTP transport for Cursor upstream.
// When upstream is nil, proxies are explicitly disabled (never inherit HTTPS_PROXY).
func newOutboundTransport(upstream *url.URL) *http.Transport {
	proxy := http.ProxyURL(upstream) // nil upstream → ProxyURL returns a func that returns nil
	if upstream == nil {
		proxy = func(*http.Request) (*url.URL, error) { return nil, nil }
	}
	return &http.Transport{
		Proxy:               proxy,
		ForceAttemptHTTP2:   true,
		MaxIdleConnsPerHost: 16,
	}
}
