package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

// Per-key IDE listeners: each Proxy Key gets its own dedicated proxy port, so
// every IDE session on that listener is attributed to that key without any
// client cooperation (Cursor cannot carry a proxy key). The main port keeps
// serving the CLI exchange flow and the server-wide -ide-pulse-key fallback.

const defaultIDEPortBase = 9100

type idePortRegistry struct {
	mu        sync.Mutex
	base      int
	byKey     map[string]int // proxy key → allocated port
	listeners map[int]net.Listener
	path      string // persistence file; empty disables persistence
	parent    *Server
}

func newIDEPortRegistry(parent *Server, base int, path string) *idePortRegistry {
	if base <= 0 {
		base = defaultIDEPortBase
	}
	return &idePortRegistry{
		base:      base,
		byKey:     map[string]int{},
		listeners: map[int]net.Listener{},
		path:      path,
		parent:    parent,
	}
}

func (r *idePortRegistry) load() {
	if r.path == "" {
		return
	}
	b, err := os.ReadFile(r.path)
	if err != nil {
		return
	}
	var m map[string]int
	if err := json.Unmarshal(b, &m); err != nil {
		log.Printf("[ide] port map %s unreadable: %v", r.path, err)
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	for key, port := range m {
		if port <= 0 || strings.TrimSpace(key) == "" {
			continue
		}
		if _, err := r.listenLocked(key, port); err != nil {
			log.Printf("[ide] reopen listener on :%d: %v", port, err)
			continue
		}
		r.byKey[key] = port
	}
	if len(r.byKey) > 0 {
		log.Printf("[ide] restored %d per-key listener(s)", len(r.byKey))
	}
}

func (r *idePortRegistry) saveLocked() {
	if r.path == "" {
		return
	}
	b, err := json.MarshalIndent(r.byKey, "", "  ")
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(r.path), 0o700); err != nil {
		log.Printf("[ide] save port map: %v", err)
		return
	}
	// The plaintext proxy keys are persisted (0600, same trust level as the
	// local -keys config) because restored listeners must be able to
	// re-authorize against Pulse after a restart.
	if err := os.WriteFile(r.path, b, 0o600); err != nil {
		log.Printf("[ide] save port map: %v", err)
	}
}

// listenLocked opens (or reuses) the listener for a port. Caller holds mu.
// The listener serves a per-proxy-key view of the parent server — see
// Server.withIDEKey.
func (r *idePortRegistry) listenLocked(key string, port int) (net.Listener, error) {
	if ln, ok := r.listeners[port]; ok {
		return ln, nil
	}
	ln, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return nil, err
	}
	r.listeners[port] = ln
	go http.Serve(ln, r.parent.withIDEKey(key))
	return ln, nil
}

// portForKey validates the proxy key (when Pulse is configured) and returns the
// key's dedicated port, allocating one on first use.
func (r *idePortRegistry) portForKey(w http.ResponseWriter, key string) (int, bool) {
	key = strings.TrimSpace(key)
	if key == "" {
		http.Error(w, "missing key", http.StatusBadRequest)
		return 0, false
	}
	if r.parent.pulse != nil {
		res, ok := authorizeOrReject(w, r.parent.pulse, key)
		if !ok {
			return 0, false
		}
		if res.ProxyKeyID == "" && res.Mode != "loan_passthrough" && res.Mode != "loan_alias" && res.Mode != "loan_pool" {
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return 0, false
		}
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	if port, ok := r.byKey[key]; ok {
		return port, true
	}
	used := map[int]bool{}
	for _, p := range r.byKey {
		used[p] = true
	}
	port := 0
	for p := r.base; p < r.base+1000; p++ {
		if !used[p] {
			port = p
			break
		}
	}
	if port == 0 {
		http.Error(w, "no free ide ports", http.StatusServiceUnavailable)
		return 0, false
	}
	if _, err := r.listenLocked(key, port); err != nil {
		log.Printf("[ide] listen :%d: %v", port, err)
		http.Error(w, "listen failed: "+err.Error(), http.StatusServiceUnavailable)
		return 0, false
	}
	r.byKey[key] = port
	r.saveLocked()
	log.Printf("[ide] allocated port %d for proxy key %s...%s", port, key[:5], key[len(key)-4:])
	return port, true
}

// Close releases every per-key listener (used by tests; production listeners
// live for the process lifetime).
func (r *idePortRegistry) Close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	for port, ln := range r.listeners {
		ln.Close()
		delete(r.listeners, port)
	}
}

// serveIDEPort answers GET /ide-port?key=... with {"port": N, "proxy_host": H}.
func (s *Server) serveIDEPort(w http.ResponseWriter, r *http.Request) {
	if s.idePorts == nil {
		http.Error(w, "ide ports disabled (requires Pulse mode)", http.StatusServiceUnavailable)
		return
	}
	port, ok := s.idePorts.portForKey(w, r.URL.Query().Get("key"))
	if !ok {
		return
	}
	host := sanitizeHostHeader(r)
	if h, _, err := net.SplitHostPort(host); err == nil && h != "" {
		host = h
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"port":       strconv.Itoa(port),
		"proxy_host": host,
	})
}

// sanitizeHostHeader returns the Host header cleaned for echo back into
// scripts/responses (port included; strip it via SplitHostPort when a bare
// host is wanted).
func sanitizeHostHeader(r *http.Request) string {
	host := r.Host
	if host == "" {
		return "127.0.0.1"
	}
	return strings.ReplaceAll(strings.TrimSpace(host), "'", "")
}
