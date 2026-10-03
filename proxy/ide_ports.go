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

// Per-key IDE listeners: each Pulse access key gets its own dedicated proxy
// port, so every IDE session on that listener is attributed to that key
// without any client cooperation (Cursor cannot carry a proxy key). The main
// port keeps serving the CLI exchange flow and the server-wide -ide-pulse-key
// fallback.

const defaultIDEPortBase = 9100

type idePortRegistry struct {
	mu        sync.Mutex
	base      int
	byKey     map[string]int    // pulse key → allocated port
	byPort    map[int]string    // port → pulse key
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
		byPort:    map[int]string{},
		listeners: map[int]net.Listener{},
		path:      path,
		parent:    parent,
	}
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
			log.Printf("[ide] reopen listener for %s...%s on :%d: %v",
				maskClientToken(key), key[max(0, len(key)-4):], port, err)
			continue
		}
		r.byKey[key] = port
		r.byPort[port] = key
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
	if err := os.WriteFile(r.path, b, 0o600); err != nil {
		log.Printf("[ide] save port map: %v", err)
	}
}

// listenLocked opens (or reuses) the listener for a port. Caller holds mu.
func (r *idePortRegistry) listenLocked(key string, port int) (net.Listener, error) {
	if ln, ok := r.listeners[port]; ok {
		return ln, nil
	}
	ln, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return nil, err
	}
	r.listeners[port] = ln
	// A per-key view of the parent server (explicit field copy — Server holds
	// a mutex and must never be copied by value): every session accepted here
	// binds to this key, sharing pool, sessions and sticky state. passthrough
	// stays nil so the clone lazily builds its own loan-key cache.
	p := r.parent
	srv := &Server{
		pool:             p.pool,
		ca:               p.ca,
		pulse:            p.pulse,
		sessions:         p.sessions,
		sticky:           p.sticky,
		sessionTTL:       p.sessionTTL,
		onRotate:         p.onRotate,
		transport:        p.transport,
		shouldMITM:       p.shouldMITM,
		connectAllowlist: p.connectAllowlist,
		idePulseKey:      key,
		caPEMPath:        p.caPEMPath,
		idePorts:         p.idePorts,
	}
	go http.Serve(ln, srv)
	return ln, nil
}

// portForKey validates the pulse key (when Pulse is configured) and returns the
// key's dedicated port, allocating one on first use.
func (r *idePortRegistry) portForKey(w http.ResponseWriter, key string) (int, bool) {
	key = strings.TrimSpace(key)
	if key == "" {
		http.Error(w, "missing key", http.StatusBadRequest)
		return 0, false
	}
	if r.parent.pulse != nil {
		res, err := r.parent.pulse.Authorize(key)
		if err != nil {
			log.Printf("[ide] port alloc authorize fail-closed: %v", err)
			http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
			return 0, false
		}
		switch res.Status {
		case "ok", "window_limited":
		case "invalid":
			http.Error(w, "invalid key", http.StatusUnauthorized)
			return 0, false
		case "suspended":
			msg := "suspended"
			if res.Reason != nil {
				msg = *res.Reason
			}
			http.Error(w, msg, http.StatusForbidden)
			return 0, false
		default:
			http.Error(w, "authorize rejected", http.StatusForbidden)
			return 0, false
		}
		if res.ProxyKeyID == "" && res.Mode != "loan_passthrough" && res.Mode != "loan_alias" {
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
	r.byPort[port] = key
	r.saveLocked()
	log.Printf("[ide] allocated port %d for key %s...%s", port, maskClientToken(key), key[max(0, len(key)-4):])
	return port, true
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
	host := requestHost(r)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"port":       strconv.Itoa(port),
		"proxy_host": host,
	})
}

func requestHost(r *http.Request) string {
	host := r.Host
	if host == "" {
		return "127.0.0.1"
	}
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	return strings.ReplaceAll(strings.TrimSpace(host), "'", "")
}
