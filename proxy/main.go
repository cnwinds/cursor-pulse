package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func main() {
	log.SetPrefix("[cursor-quota-proxy] ")
	bootstrapDockerDotenv()
	var (
		listen         = flag.String("listen", "", "listen address (default 0.0.0.0:8317)")
		keys           = flag.String("keys", "", "comma-separated Cursor API keys (saved to config)")
		dir            = flag.String("dir", "", "state directory (default ~/.cursor-quota-proxy)")
		conf           = flag.String("config", "", "config file path (default <dir>/config.json)")
		pulseURL       = flag.String("pulse-url", "", "Pulse control-plane base URL (env PULSE_BASE_URL)")
		pulseToken     = flag.String("pulse-token", "", "Pulse internal service token (env PULSE_INTERNAL_SERVICE_TOKEN)")
		upstreamProxy  = flag.String("upstream-proxy", "", "HTTP(S) proxy for Cursor upstream (env PROXY_UPSTREAM_URL)")
		sessionTTL     = flag.Duration("session-ttl", 0, "session re-authorize interval (default 120s; env PROXY_SESSION_TTL)")
		stickyMinDwell = flag.Duration("sticky-min-dwell", 0, stickyMinDwellUsage)
		idePulseKey    = flag.String("ide-pulse-key", "", "proxy key (pk_/pka_) that IDE-originated sessions bind to (env PROXY_IDE_PULSE_KEY, config ide_pulse_key)")
		idePortBase    = flag.Int("ide-port-base", 0, "first port for per-key IDE listeners (env PROXY_IDE_PORT_BASE, default 9100; Pulse mode only)")
	)
	flag.Parse()

	stateDir := *dir
	if stateDir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			log.Fatalf("cannot locate home dir: %v", err)
		}
		stateDir = filepath.Join(home, ".cursor-quota-proxy")
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		log.Fatalf("create state dir: %v", err)
	}

	cfgPath := *conf
	if cfgPath == "" {
		cfgPath = filepath.Join(stateDir, "config.json")
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		log.Fatalf("load config %s: %v", cfgPath, err)
	}
	if *keys != "" {
		cfg.Keys = splitKeys(*keys)
		if err := cfg.save(cfgPath); err != nil {
			log.Printf("warning: could not save config: %v", err)
		}
	}
	if *listen != "" {
		cfg.Listen = *listen
	} else if v := strings.TrimSpace(os.Getenv("PROXY_LISTEN")); v != "" {
		cfg.Listen = v
	}
	if cfg.Listen == "" {
		cfg.Listen = "0.0.0.0:8317"
	}

	resolvedPulseURL := firstNonEmpty(*pulseURL, cfg.PulseURL, os.Getenv("PULSE_BASE_URL"))
	resolvedPulseTok := firstNonEmpty(*pulseToken, cfg.PulseToken, os.Getenv("PULSE_INTERNAL_SERVICE_TOKEN"))
	pulseMode := resolvedPulseURL != "" && resolvedPulseTok != ""

	if !pulseMode && len(cfg.Keys) == 0 {
		fmt.Fprintf(os.Stderr, `No Cursor API keys configured.

Run once with your keys (they will be saved to %s):

  cursor-quota-proxy -keys "key1,key2,key3"

Or configure Pulse control plane:

  cursor-quota-proxy -pulse-url http://127.0.0.1:8000 -pulse-token <token>

`, cfgPath)
		os.Exit(2)
	}

	ca, caPEMPath, created, err := loadOrCreateCA(stateDir)
	if err != nil {
		log.Fatalf("CA: %v", err)
	}
	if created {
		fmt.Fprintf(os.Stderr, `Generated a new MITM root CA at:

  %s

Point agent at this proxy and trust the CA (PowerShell):

  $env:HTTPS_PROXY = "http://%s"
  $env:NODE_EXTRA_CA_CERTS = "%s"
  agent

(Alternatively pass -k / --insecure to agent instead of NODE_EXTRA_CA_CERTS.)

`, caPEMPath, cfg.Listen, caPEMPath)
	}

	var pulse *PulseClient
	var pool *Pool
	var sessions *SessionMap

	if pulseMode {
		pulse = NewPulseClient(resolvedPulseURL, resolvedPulseTok, 60*time.Second)
		pulse.Start()
		defer pulse.Stop()
		pool = NewPoolFromCredentials(nil)
		sessions = NewSessionMap()
		every := 60 * time.Second
		if cfg.PoolEvery != "" {
			if d, err := time.ParseDuration(cfg.PoolEvery); err == nil && d > 0 {
				every = d
			}
		}
		go pollPool(pool, pulse, every)
		log.Printf("listening on %s (Pulse mode: %s)", cfg.Listen, resolvedPulseURL)
	} else {
		// Local -keys mode: no session gate (sessions=nil).
		pool = NewPool(cfg.Keys)
		log.Printf("listening on %s with %d API key(s)", cfg.Listen, len(cfg.Keys))
	}

	exhaustedReset := resolveExhaustedResetInterval()
	if exhaustedReset > 0 {
		go pollExhaustedReset(pool, exhaustedReset)
		log.Printf("pool exhausted reset every %s (PROXY_EXHAUSTED_RESET)", exhaustedReset)
	} else {
		log.Printf("pool exhausted reset disabled (PROXY_EXHAUSTED_RESET=0/off)")
	}

	srv := NewServer(pool, ca, pulse, sessions)
	if pulseMode {
		srv.sessionTTL = resolveSessionTTL(*sessionTTL)
		srv.sticky = NewStickySelectWithDwell(pool, sessions, resolveStickyMinDwell(*stickyMinDwell))
		srv.useSeatAdvisor()
		if sessionTokensEnabled() {
			log.Printf("exchange issues proxy-minted session tokens (PROXY_OPAQUE_SESSION_TOKEN)")
		} else {
			srv.sessionTokens = nil
			log.Printf("WARNING: PROXY_OPAQUE_SESSION_TOKEN=off - exchange returns upstream Cursor JWTs to clients")
		}
		go pruneSessions(sessions, 10*time.Minute)
	}
	srv.caPEMPath = caPEMPath
	srv.idePulseKey = firstNonEmpty(*idePulseKey, os.Getenv("PROXY_IDE_PULSE_KEY"), cfg.IdePulseKey)
	if srv.idePulseKey != "" && !pulseMode {
		log.Fatalf("ide-pulse-key requires Pulse mode (-pulse-url/-pulse-token or config)")
	}
	if srv.idePulseKey != "" {
		log.Printf("IDE mode: unbound sessions bind to pulse key %s...%s",
			srv.idePulseKey[:min(4, len(srv.idePulseKey))], srv.idePulseKey[max(0, len(srv.idePulseKey)-4):])
	}
	srv.ideLockSubInit(resolveIdeLockSub())
	if pulseMode {
		base := *idePortBase
		if base <= 0 {
			if v, err := strconv.Atoi(strings.TrimSpace(os.Getenv("PROXY_IDE_PORT_BASE"))); err == nil && v > 0 {
				base = v
			} else {
				base = defaultIDEPortBase
			}
		}
		reg := newIDEPortRegistry(srv, base, filepath.Join(stateDir, "ide_ports.json"))
		srv.idePorts = reg
		reg.load()
	}

	upstreamRaw := firstNonEmpty(*upstreamProxy, os.Getenv("PROXY_UPSTREAM_URL"))
	upstream, err := parseUpstreamProxy(upstreamRaw)
	if err != nil {
		log.Fatalf("upstream proxy: %v", err)
	}
	if upstream != nil {
		pool.SetUpstreamProxy(upstream)
		srv.SetUpstreamProxy(upstream)
	}
	log.Printf("cursor upstream: %s", redactUpstreamProxy(upstreamRaw))

	httpSrv := &http.Server{
		Addr:              cfg.Listen,
		Handler:           srv,
		ReadHeaderTimeout: defaultReadHeaderTimeout,
		IdleTimeout:       defaultIdleTimeout,
	}
	log.Fatal(httpSrv.ListenAndServe())
}

func pollExhaustedReset(pool *Pool, every time.Duration) {
	tick := time.NewTicker(every)
	defer tick.Stop()
	for range tick.C {
		// Only runtime Quota Pool marks — auth cooldowns keep their own TTL.
		pool.resetQuotaMarks()
	}
}

func pruneSessions(sessions *SessionMap, every time.Duration) {
	tick := time.NewTicker(every)
	defer tick.Stop()
	for range tick.C {
		if n := sessions.Prune(time.Now()); n > 0 {
			log.Printf("[session] pruned %d expired session(s)", n)
		}
	}
}

func pollPool(pool *Pool, pulse *PulseClient, every time.Duration) {
	tick := time.NewTicker(every)
	defer tick.Stop()
	refresh := func() {
		snap, err := pulse.FetchPool()
		if err != nil {
			log.Printf("[pool] fetch: %v", err)
			return
		}
		pool.ReplaceFromPulseSnapshot(snap)
	}
	refresh()
	for range tick.C {
		refresh()
	}
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

const defaultSessionTTL = 120 * time.Second

// defaultStickyMinDwell is the Switch dwell default: a CLI session keeps its
// sticky credential for at least this long before quota pressure may rotate it.
const defaultStickyMinDwell = 20 * time.Minute

// stickyMinDwellUsage is the flag/env help text for Switch dwell.
const stickyMinDwellUsage = "min time a CLI session keeps its sticky credential " +
	"(default 20m; env PROXY_STICKY_MIN_DWELL, 0/off disables)"

func resolveStickyMinDwell(flagVal time.Duration) time.Duration {
	if flagVal > 0 {
		return flagVal
	}
	// 显式 0 / off 关闭驻留（与未配置区分开）
	switch strings.ToLower(strings.TrimSpace(os.Getenv("PROXY_STICKY_MIN_DWELL"))) {
	case "0", "off", "false", "no":
		return 0
	}
	if d, ok := parseDurationValue(os.Getenv("PROXY_STICKY_MIN_DWELL")); ok && d >= 0 {
		return d
	}
	return defaultStickyMinDwell
}

func resolveSessionTTL(flagVal time.Duration) time.Duration {
	if flagVal > 0 {
		return flagVal
	}
	if d, ok := parseDurationValue(os.Getenv("PROXY_SESSION_TTL")); ok && d > 0 {
		return d
	}
	return defaultSessionTTL
}

func resolveIdeLockSub() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("PROXY_IDE_LOCK_SUB"))) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}
