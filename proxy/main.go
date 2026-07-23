package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

func main() {
	log.SetPrefix("[cursor-quota-proxy] ")
	var (
		listen         = flag.String("listen", "", "listen address (default 0.0.0.0:8317)")
		keys           = flag.String("keys", "", "comma-separated Cursor API keys (saved to config)")
		dir            = flag.String("dir", "", "state directory (default ~/.cursor-quota-proxy)")
		conf           = flag.String("config", "", "config file path (default <dir>/config.json)")
		pulseURL       = flag.String("pulse-url", "", "Pulse control-plane base URL (env PULSE_BASE_URL)")
		pulseToken     = flag.String("pulse-token", "", "Pulse internal service token (env PULSE_INTERNAL_SERVICE_TOKEN)")
		upstreamProxy  = flag.String("upstream-proxy", "", "HTTP(S) proxy for Cursor upstream (env PROXY_UPSTREAM_URL)")
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

	srv := NewServer(pool, ca, pulse, sessions)

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

	log.Fatal(http.ListenAndServe(cfg.Listen, srv))
}

func pollPool(pool *Pool, pulse *PulseClient, every time.Duration) {
	tick := time.NewTicker(every)
	defer tick.Stop()
	refresh := func() {
		creds, err := pulse.FetchPool()
		if err != nil {
			log.Printf("[pool] fetch: %v", err)
			return
		}
		pool.ReplaceFromPulse(creds)
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
