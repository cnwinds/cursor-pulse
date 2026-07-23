package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
)

var hopHeaders = map[string]bool{
	"Connection":          true,
	"Proxy-Connection":    true,
	"Keep-Alive":          true,
	"Proxy-Authenticate":  true,
	"Proxy-Authorization": true,
	"Te":                  true,
	"Trailer":             true,
	"Transfer-Encoding":   true,
	"Upgrade":             true,
}

// handleMITM processes a single decrypted request destined for a Cursor
// backend: it rewrites the Authorization header with the pool's current
// token, watches the response for quota/auth failures, and transparently
// replays the request with the next key when the current account is done.
func (s *Server) handleMITM(w http.ResponseWriter, req *http.Request, authority string) {
	defer req.Body.Close()

	// Pulse-mode exchange: authorize pulse key, mint pool JWT, bind session.
	// Without Pulse, fall through so local -keys mode can still passthrough.
	if s.pulse != nil && req.Method == http.MethodPost && req.URL.Path == exchangePath {
		s.handleExchange(w, req)
		return
	}

	reqCT := req.Header.Get("Content-Type")
	isStreamReq := strings.HasPrefix(reqCT, "application/connect")
	skipAuth := strings.HasPrefix(req.URL.Path, "/auth/")
	target := "https://" + authority + req.URL.RequestURI()

	// Prepare a replayable body source + snapshot for model extraction.
	var bodyFor func() io.ReadCloser
	var reqBodySnap func() []byte
	if isStreamReq {
		fs := newFrameSource(req.Body)
		bodyFor = func() io.ReadCloser { return fs.reader() }
		reqBodySnap = fs.snapshot
	} else {
		body, err := io.ReadAll(io.LimitReader(req.Body, 1<<30))
		if err != nil {
			http.Error(w, "read request body: "+err.Error(), http.StatusBadRequest)
			return
		}
		bodyFor = func() io.ReadCloser {
			if len(body) == 0 {
				return http.NoBody
			}
			return io.NopCloser(bytes.NewReader(body))
		}
		reqBodySnap = func() []byte { return body }
	}

	// Pulse sessions gate: business requests must present a previously bound JWT.
	// sessions == nil skips the check (local -keys / baseline tests).
	var binding SessionBinding
	if s.sessions != nil && !skipAuth {
		cliTok := strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
		cliTok = strings.TrimPrefix(cliTok, "bearer ")
		b, ok := s.sessions.Lookup(cliTok)
		if !ok {
			http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
			return
		}
		binding = b
	}

	loanBound := binding.Mode == "loan_passthrough" || binding.Mode == "loan_alias"
	maxAttempts := s.pool.size()
	if maxAttempts < 1 {
		maxAttempts = 1
	}
	if loanBound {
		maxAttempts = 1
	}

	for attempt := 0; attempt < maxAttempts; attempt++ {
		last := attempt == maxAttempts-1

		var entry *keyEntry
		var token string
		var err error
		if loanBound {
			entry, token, err = s.passthroughToken(req.Context(), binding)
		} else {
			entry, token, err = s.pool.token(req.Context())
		}
		if err != nil {
			log.Printf("[mitm] %s %s: %v", req.Method, req.URL.Path, err)
			if s.pulse != nil {
				ev := EventItem{EventType: "exhausted", Detail: err.Error()}
				if loanBound {
					ev.LoanID = binding.LoanID
					ev.CredentialID = binding.CredentialID
				} else {
					ev.ProxyKeyID = binding.ProxyKeyID
				}
				s.pulse.ReportEvent(ev)
			}
			msg := "cursor-quota-proxy: all API keys exhausted"
			if loanBound {
				msg = "cursor-pulse-proxy: loan key unavailable"
			}
			http.Error(w, msg, http.StatusServiceUnavailable)
			return
		}

		outReq, err := http.NewRequestWithContext(req.Context(), req.Method, target, bodyFor())
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		copyHeaders(outReq.Header, req.Header)
		outReq.Header.Del("Accept-Encoding")
		if !skipAuth {
			outReq.Header.Set("Authorization", "Bearer "+token)
		}

		resp, err := s.transport.RoundTrip(outReq)
		if err != nil {
			log.Printf("[mitm] %s %s attempt %d (key %s): transport error: %v",
				req.Method, req.URL.Path, attempt+1, entry.masked(), err)
			if last {
				http.Error(w, "cursor-quota-proxy: upstream unreachable: "+err.Error(), http.StatusBadGateway)
				return
			}
			continue // same key retried on next attempt (not marked bad)
		}

		// --- non-200: whole-body classification ---
		if resp.StatusCode != http.StatusOK {
			body, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
			resp.Body.Close()
			kind := classifyHTTPError(resp.StatusCode, body)
			if shouldRotateOnFailure(req.URL.Path, kind) && !last {
				if loanBound {
					s.reportPassthroughFailure(entry, kind, binding)
				} else {
					s.mark(entry, kind, binding)
				}
				log.Printf("[mitm] %s %s attempt %d (key %s): HTTP %d classified %s - rotating",
					req.Method, req.URL.Path, attempt+1, entry.masked(), resp.StatusCode, kind)
				continue
			}
			if loanBound && shouldRotateOnFailure(req.URL.Path, kind) && kind == failAuth {
				s.reportPassthroughFailure(entry, kind, binding)
			}
			if kind == failAuth && isNonFatalAuthPath(req.URL.Path) {
				log.Printf("[mitm] %s %s attempt %d (key %s): HTTP %d auth ignored (non-fatal path)",
					req.Method, req.URL.Path, attempt+1, entry.masked(), resp.StatusCode)
			}
			copyHeaders(w.Header(), resp.Header)
			w.WriteHeader(resp.StatusCode)
			w.Write(body)
			return
		}

		// --- 200 with Connect streaming body: inspect first envelope ---
		respCT := resp.Header.Get("Content-Type")
		if strings.HasPrefix(respCT, "application/connect") {
			flags, payload, err := readEnvelope(resp.Body)
			if err != nil {
				// Stream died before any envelope: retry if we can.
				resp.Body.Close()
				log.Printf("[mitm] %s %s attempt %d (key %s): stream ended before first envelope: %v",
					req.Method, req.URL.Path, attempt+1, entry.masked(), err)
				if last {
					copyHeaders(w.Header(), resp.Header)
					w.WriteHeader(http.StatusOK)
					return
				}
				continue
			}
			if flags&endStreamFlag != 0 {
				kind := classifyEndStream(payload)
				resp.Body.Close()
				if shouldRotateOnFailure(req.URL.Path, kind) && !last {
					if loanBound {
						s.reportPassthroughFailure(entry, kind, binding)
					} else {
						s.mark(entry, kind, binding)
					}
					log.Printf("[mitm] %s %s attempt %d (key %s): end-stream error classified %s - rotating",
						req.Method, req.URL.Path, attempt+1, entry.masked(), kind)
					continue
				}
				if loanBound && shouldRotateOnFailure(req.URL.Path, kind) && kind == failAuth {
					s.reportPassthroughFailure(entry, kind, binding)
				}
				if kind == failAuth && isNonFatalAuthPath(req.URL.Path) {
					log.Printf("[mitm] %s %s attempt %d (key %s): end-stream auth ignored (non-fatal path)",
						req.Method, req.URL.Path, attempt+1, entry.masked())
				}
				// Terminal response (clean end or non-rotatable error): forward whole.
				copyHeaders(w.Header(), resp.Header)
				w.WriteHeader(http.StatusOK)
				writeEnvelope(w, flags, payload)
				return
			}
			// First envelope is data - the run is live. Flush and switch to
			// full passthrough; mid-stream failures fall through to the CLI.
			copyHeaders(w.Header(), resp.Header)
			w.WriteHeader(http.StatusOK)
			onTok := func(tc TokenCounts) {
				if s.pulse == nil {
					return
				}
				var body []byte
				if reqBodySnap != nil {
					body = reqBodySnap()
				}
				if loanBound {
					if binding.LoanID == "" {
						return
					}
					model := logUsageModelTap(req.URL.Path, "", binding.CredentialID, tc, body)
					s.pulse.EnqueueUsage(UsageItem{
						LoanID:       binding.LoanID,
						CredentialID: binding.CredentialID,
						Model:        model,
						Tokens:       tc,
					})
					return
				}
				if binding.ProxyKeyID == "" {
					return
				}
				model := logUsageModelTap(req.URL.Path, binding.ProxyKeyID, entry.credentialID, tc, body)
				s.pulse.EnqueueUsage(UsageItem{
					ProxyKeyID:   binding.ProxyKeyID,
					CredentialID: entry.credentialID,
					Model:        model,
					Tokens:       tc,
				})
			}
			// First data envelope is written outside the tap; scan it too.
			if tok := findTurnEnded(payload); tok != nil {
				onTok(*tok)
			}
			if err := writeEnvelope(w, flags, payload); err != nil {
				resp.Body.Close()
				return
			}
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
			io.Copy(&usageTapWriter{w: flushWriter{w: w}, onTokens: onTok}, resp.Body)
			resp.Body.Close()
			return
		}

		// --- 200 unary: plain passthrough ---
		copyHeaders(w.Header(), resp.Header)
		w.WriteHeader(http.StatusOK)
		io.Copy(w, resp.Body)
		resp.Body.Close()
		return
	}
}

func (s *Server) handleExchange(w http.ResponseWriter, req *http.Request) {
	io.Copy(io.Discard, io.LimitReader(req.Body, 1<<20))

	auth := strings.TrimSpace(req.Header.Get("Authorization"))
	pulseKey := strings.TrimPrefix(auth, "Bearer ")
	pulseKey = strings.TrimPrefix(pulseKey, "bearer ")
	if pulseKey == "" || pulseKey == auth {
		http.Error(w, "missing pulse key", http.StatusUnauthorized)
		return
	}
	if s.pulse == nil {
		http.Error(w, "pulse client not configured", http.StatusServiceUnavailable)
		return
	}
	res, err := s.pulse.Authorize(pulseKey)
	if err != nil {
		log.Printf("[mitm] authorize fail-closed: %v", err)
		http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
		return
	}
	switch res.Status {
	case "invalid":
		http.Error(w, "invalid pulse key", http.StatusUnauthorized)
		return
	case "suspended":
		msg := "suspended"
		if res.Reason != nil {
			msg = *res.Reason
		}
		http.Error(w, msg, http.StatusForbidden)
		return
	case "window_limited":
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"code":"resource_exhausted","message":"5h window limited; retry later"}`))
		return
	case "ok":
		// continue
	default:
		http.Error(w, "authorize rejected", http.StatusForbidden)
		return
	}

	if res.Mode == "loan_passthrough" || res.Mode == "loan_alias" {
		exchCtx, cancel := context.WithTimeout(req.Context(), exchangeTimeout)
		defer cancel()
		exchangeKey := pulseKey
		if res.Mode == "loan_alias" {
			exchangeKey = strings.TrimSpace(res.CursorAPIKey)
			if exchangeKey == "" {
				log.Printf("[mitm] loan_alias missing cursor_api_key loan_id=%s", res.LoanID)
				http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
				return
			}
		}
		token, err := exchangeCursorAPIKey(exchCtx, s.pool.client, s.pool.exchangeBase, exchangeKey)
		if err != nil {
			if isPermanentExchangeErr(err) {
				http.Error(w, "cursor key exchange failed", http.StatusUnauthorized)
				return
			}
			log.Printf("[mitm] loan %s exchange fail: %v", res.Mode, err)
			http.Error(w, "cursor key exchange unavailable", http.StatusServiceUnavailable)
			return
		}
		if s.sessions != nil {
			s.sessions.Bind(token, SessionBinding{
				Mode:         res.Mode,
				LoanID:       res.LoanID,
				CredentialID: res.CredentialID,
				PulseKey:     pulseKey,
				CursorAPIKey: exchangeKey,
			})
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  token,
			"refreshToken": "pulse",
		})
		log.Printf("[mitm] exchange ok %s loan_id=%s credential=%s", res.Mode, res.LoanID, res.CredentialID)
		return
	}

	if res.ProxyKeyID == "" {
		log.Printf("[mitm] authorize ok but missing proxy_key_id mode=%q — refuse pool path", res.Mode)
		http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
		return
	}

	entry, token, err := s.pool.token(req.Context())
	if err != nil {
		s.pulse.ReportEvent(EventItem{EventType: "exhausted", ProxyKeyID: res.ProxyKeyID, Detail: err.Error()})
		http.Error(w, "cursor-pulse-proxy: all API keys exhausted", http.StatusServiceUnavailable)
		return
	}
	// Pool may return a JWT already bound to a different pulse key.
	// Skip that credential and try other pool keys (same ProxyKeyID may re-bind).
	if s.sessions != nil {
		skip := map[string]bool{}
		maxTries := s.pool.size()
		if maxTries < 1 {
			maxTries = 1
		}
		for tries := 0; tries < maxTries; tries++ {
			if b, ok := s.sessions.Lookup(token); ok && b.ProxyKeyID != "" && b.ProxyKeyID != res.ProxyKeyID {
				skip[entry.credentialID] = true
				log.Printf("[mitm] exchange jwt collision proxy_key=%s held_by=%s skip_credential=%s",
					res.ProxyKeyID, b.ProxyKeyID, entry.credentialID)
				entry, token, err = s.pool.tokenSkipping(req.Context(), skip)
				if err != nil {
					s.pulse.ReportEvent(EventItem{EventType: "exhausted", ProxyKeyID: res.ProxyKeyID, Detail: err.Error()})
					http.Error(w, "cursor-pulse-proxy: all API keys exhausted", http.StatusServiceUnavailable)
					return
				}
				continue
			}
			break
		}
		if b, ok := s.sessions.Lookup(token); ok && b.ProxyKeyID != "" && b.ProxyKeyID != res.ProxyKeyID {
			log.Printf("[mitm] exchange jwt collision unresolved proxy_key=%s held_by=%s", res.ProxyKeyID, b.ProxyKeyID)
			http.Error(w, "cursor-pulse-proxy: unable to mint unique session token", http.StatusServiceUnavailable)
			return
		}
		s.sessions.Bind(token, SessionBinding{ProxyKeyID: res.ProxyKeyID, PulseKey: pulseKey})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"accessToken":  token,
		"refreshToken": "pulse",
	})
	log.Printf("[mitm] exchange ok proxy_key=%s credential=%s", res.ProxyKeyID, entry.credentialID)
}

func (s *Server) reportPassthroughFailure(entry *keyEntry, kind failKind, binding SessionBinding) {
	if kind == failAuth {
		entry.invalidate()
	}
	if s.pulse != nil {
		s.pulse.ReportEvent(EventItem{
			EventType:    "rotation",
			LoanID:       binding.LoanID,
			CredentialID: binding.CredentialID,
			Detail:       kind.String(),
		})
	}
}

func (s *Server) mark(entry *keyEntry, kind failKind, binding SessionBinding) {
	if kind == failAuth {
		s.pool.markBad(entry)
	} else {
		s.pool.markExhausted(entry)
	}
	if s.pulse != nil {
		s.pulse.ReportEvent(EventItem{
			EventType:    "rotation",
			ProxyKeyID:   binding.ProxyKeyID,
			CredentialID: entry.credentialID,
			Detail:       kind.String(),
		})
	}
	if s.onRotate != nil {
		s.onRotate(entry, binding, kind)
	}
}

func copyHeaders(dst, src http.Header) {
	for k, vs := range src {
		if hopHeaders[http.CanonicalHeaderKey(k)] {
			continue
		}
		dst.Del(k)
		for _, v := range vs {
			dst.Add(k, v)
		}
	}
}

// flushWriter flushes after every write so streaming frames reach the CLI
// with minimal latency.
type flushWriter struct {
	w io.Writer
}

func (fw flushWriter) Write(p []byte) (int, error) {
	n, err := fw.w.Write(p)
	if f, ok := fw.w.(http.Flusher); ok {
		f.Flush()
	}
	return n, err
}
