package main

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type Server struct {
	pool      *Pool
	ca        *CA
	pulse     *PulseClient
	sessions  *SessionMap
	onRotate  func(entry *keyEntry, binding SessionBinding, kind failKind)
	transport *http.Transport

	passthroughMu sync.Mutex
	passthrough   map[string]*keyEntry // credentialID → cached loan key JWT

	// shouldMITM reports whether a CONNECT target's TLS should be intercepted
	// (true for Cursor backends); other hosts are tunneled blindly.
	shouldMITM func(authority string) bool
}

func NewServer(pool *Pool, ca *CA, pulse *PulseClient, sessions *SessionMap) *Server {
	return &Server{
		pool:       pool,
		ca:         ca,
		pulse:      pulse,
		sessions:   sessions,
		transport:  newOutboundTransport(nil),
		shouldMITM: defaultShouldMITM,
	}
}

// SetUpstreamProxy routes MITM upstream (Cursor) traffic via the given proxy.
func (s *Server) SetUpstreamProxy(upstream *url.URL) {
	s.transport = newOutboundTransport(upstream)
}

func defaultShouldMITM(authority string) bool {
	host := authority
	if h, _, err := net.SplitHostPort(authority); err == nil {
		host = h
	}
	host = strings.ToLower(host)
	return host == "cursor.sh" || strings.HasSuffix(host, ".cursor.sh")
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodConnect {
		http.Error(w, "cursor-quota-proxy: CONNECT only", http.StatusBadRequest)
		return
	}
	s.handleConnect(w, r)
}

func (s *Server) handleConnect(w http.ResponseWriter, r *http.Request) {
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijacking unsupported", http.StatusInternalServerError)
		return
	}
	client, _, err := hj.Hijack()
	if err != nil {
		return
	}
	authority := r.Host

	if !s.shouldMITM(authority) {
		// Blind tunnel for non-Cursor traffic.
		upstream, err := net.DialTimeout("tcp", authority, 15*time.Second)
		if err != nil {
			writeHTTPError(client, http.StatusBadGateway)
			client.Close()
			return
		}
		if _, err := client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n")); err != nil {
			upstream.Close()
			client.Close()
			return
		}
		go tunnel(upstream, client)
		return
	}

	host := authority
	if h, _, err := net.SplitHostPort(authority); err == nil {
		host = h
	}
	leaf, err := s.ca.certFor(host)
	if err != nil {
		log.Printf("[mitm] cert for %s: %v", host, err)
		writeHTTPError(client, http.StatusInternalServerError)
		client.Close()
		return
	}
	tlsConf := &tls.Config{
		Certificates: []tls.Certificate{leaf},
		NextProtos:   []string{"h2", "http/1.1"},
		MinVersion:   tls.VersionTLS12,
	}
	if _, err := client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n")); err != nil {
		client.Close()
		return
	}
	tlsConn := tls.Server(client, tlsConf)

	// Serve this single connection as an HTTP server (h2 via ALPN, or h1).
	srv := &http.Server{
		Handler:   http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) { s.handleMITM(w, req, authority) }),
		TLSConfig: tlsConf,
	}
	go srv.Serve(&oneConnListener{conn: tlsConn})
}

func tunnel(dst, src net.Conn) {
	done := make(chan struct{}, 2)
	go func() { io.Copy(dst, src); done <- struct{}{} }()
	go func() { io.Copy(src, dst); done <- struct{}{} }()
	<-done
	dst.Close()
	src.Close()
}

func writeHTTPError(c net.Conn, status int) {
	c.Write([]byte("HTTP/1.1 " + http.StatusText(status) + "\r\nContent-Length: 0\r\n\r\n"))
}

// oneConnListener adapts a single net.Conn to net.Listener for http.Server.
type oneConnListener struct {
	conn net.Conn
	done bool
}

func (l *oneConnListener) Accept() (net.Conn, error) {
	if l.done {
		return nil, io.EOF
	}
	l.done = true
	return l.conn, nil
}

func (l *oneConnListener) Close() error   { return nil }
func (l *oneConnListener) Addr() net.Addr { return l.conn.LocalAddr() }

// passthroughToken returns a JWT for a loan-bound session (passthrough or alias),
// caching by credential ID and re-exchanging the Cursor API key when needed.
// For loan_alias, re-authorizes with Pulse so revoked loans cannot keep exchanging.
func (s *Server) passthroughToken(ctx context.Context, binding SessionBinding) (*keyEntry, string, error) {
	apiKey := binding.PulseKey
	if binding.Mode == "loan_alias" {
		if s.pulse == nil || strings.TrimSpace(binding.PulseKey) == "" {
			return nil, "", fmt.Errorf("loan_alias re-authorize unavailable")
		}
		res, err := s.pulse.Authorize(binding.PulseKey)
		if err != nil {
			return nil, "", err
		}
		if res.Status != "ok" || res.Mode != "loan_alias" {
			return nil, "", fmt.Errorf("loan_alias unauthorized: status=%s mode=%s", res.Status, res.Mode)
		}
		apiKey = strings.TrimSpace(res.CursorAPIKey)
		if apiKey == "" {
			return nil, "", fmt.Errorf("loan_alias missing cursor_api_key")
		}
	}

	s.passthroughMu.Lock()
	if s.passthrough == nil {
		s.passthrough = map[string]*keyEntry{}
	}
	entry, ok := s.passthrough[binding.CredentialID]
	if !ok {
		entry = &keyEntry{
			credentialID: binding.CredentialID,
			apiKey:       apiKey,
		}
		s.passthrough[binding.CredentialID] = entry
	} else if apiKey != "" {
		entry.apiKey = apiKey
	}
	s.passthroughMu.Unlock()

	exchCtx, cancel := context.WithTimeout(context.Background(), exchangeTimeout)
	defer cancel()
	tok, err := entry.ensureToken(exchCtx, s.pool.client, s.pool.exchangeBase)
	if err != nil {
		return entry, "", err
	}
	return entry, tok, nil
}
