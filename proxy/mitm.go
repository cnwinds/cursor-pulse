package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
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
// token, watches the response for quota/auth failures, marks exhausted keys,
// and advances the pool so the next client request uses a fresh account.
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
	if debugHeadersEnabled() {
		if req.URL.Path == "/agent.v1.AgentService/RunSSE" {
			log.Printf("[hdr] %s %s ct=%q clen=%q te=%q expect=%q checksum=%q client-key=%q",
				req.Method, req.URL.Path, reqCT, req.Header.Get("Content-Length"),
				req.Header.Get("Transfer-Encoding"), req.Header.Get("Expect"),
				truncateForLog(req.Header.Get("x-cursor-checksum"), 80),
				truncateForLog(req.Header.Get("x-client-key"), 40))
		} else {
			log.Printf("[hdr] %s %s checksum=%q client-key=%q",
				req.Method, req.URL.Path,
				truncateForLog(req.Header.Get("x-cursor-checksum"), 160),
				truncateForLog(req.Header.Get("x-client-key"), 60))
		}
	}
	// skipAuth = forward the client's own Authorization untouched. Beyond the
	// /auth/ family this covers the IDE identity RPCs: Cursor IDE sends its own
	// WorkOS login JWT there, and rewriting it to a pool credential breaks the
	// client's identity-consistency checks (GetMe retries in a tight loop) and
	// lets team-scope 401s burn pool credentials.
	skipAuth := strings.HasPrefix(req.URL.Path, "/auth/") || ideIdentityPassthrough(req.URL.Path)
	if debugHTTP {
		log.Printf("[mitm] >> %s %s stream=%v skipAuth=%v", req.Method, req.URL.Path, isStreamReq, skipAuth)
	}
	target := "https://" + authority + req.URL.RequestURI()

	// Prepare a replayable body source + snapshot for model extraction.
	var bodyFor func() io.ReadCloser
	var reqBodySnap func() []byte
	var streamFS *frameSource
	if isStreamReq {
		streamFS = newFrameSource(req.Body)
		bodyFor = func() io.ReadCloser { return streamFS.reader() }
		reqBodySnap = streamFS.snapshot
	} else {
		body, err := io.ReadAll(io.LimitReader(req.Body, maxNonStreamBodyLimit()))
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
	var cliTok string
	if s.sessions != nil && !skipAuth {
		cliTok = strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
		cliTok = strings.TrimPrefix(cliTok, "bearer ")
		b, ok := s.sessions.Lookup(cliTok)
		if !ok {
			ideB, handled := s.bindIDESession(w, cliTok)
			if !handled {
				return
			}
			b = ideB
		}
		if s.pulse != nil && s.sessionTTL > 0 && time.Since(b.BoundAt) > s.sessionTTL {
			res, err := s.pulse.Authorize(b.PulseKey)
			if err != nil {
				log.Printf("[mitm] session re-authorize fail-closed: %v", err)
				http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
				return
			}
			switch res.Status {
			case "ok":
				b.WindowLimitReason = ""
				b.BoundAt = time.Now()
				if res.CredentialID != "" {
					b.CredentialID = res.CredentialID
				}
				if res.LoanID != "" {
					b.LoanID = res.LoanID
				}
				if res.Mode != "" {
					b.Mode = res.Mode
				}
				if strings.TrimSpace(res.CursorAPIKey) != "" {
					b.CursorAPIKey = strings.TrimSpace(res.CursorAPIKey)
				}
				// Replace (not merge): a candidate dropped from the ranked
				// allowlist must stop serving on the next request. Empty clears
				// the scope, which returns the binding to the pinned path.
				b.AllowedCredentialIDs = res.CredentialIDs
				s.sessions.Bind(cliTok, b)
			case "window_limited":
				b.WindowLimitReason = authWindowReason(res)
				b.BoundAt = time.Now()
				s.sessions.Bind(cliTok, b)
				writeWindowLimited(w, b.WindowLimitReason)
				return
			default:
				s.sessions.Delete(cliTok)
				http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
				return
			}
		}
		if b.WindowLimitReason != "" {
			writeWindowLimited(w, b.WindowLimitReason)
			return
		}
		binding = b
	}

	loanBound := binding.Mode == "loan_passthrough" || binding.Mode == "loan_alias"
	// A loan_alias binding carrying a Pulse-issued candidate allowlist selects
	// among those accounts exactly like the shared pool (sticky + Switch dwell +
	// per-bucket availability) instead of being pinned to one credential. An
	// allowlist that collapses to nil (empty, or all-blank entries) keeps the
	// legacy passthrough path — the same predicate sticky.Select uses.
	loanPooled := binding.Mode == "loan_alias" && s.sticky != nil && binding.allowedSet() != nil
	quotaPool := resolveQuotaPool(req.Context(), req.URL.Path, reqBodySnap, streamFS)
	markPool := func() quotaPoolKind {
		return effectiveMarkQuotaPool(req.URL.Path, reqBodySnap, quotaPool)
	}
	transportAttempts := 3
	if loanBound && !loanPooled {
		transportAttempts = 1
	}

	var entry *keyEntry
	var resp *http.Response
	for attempt := 0; attempt < transportAttempts; attempt++ {
		var token string
		var err error
		if loanPooled {
			entry, token, err = s.sticky.Select(req.Context(), cliTok, &binding, quotaPool)
		} else if loanBound {
			entry, token, err = s.passthroughToken(req.Context(), binding)
		} else if s.sticky != nil {
			entry, token, err = s.sticky.Select(req.Context(), cliTok, &binding, quotaPool)
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

		resp, err = s.transport.RoundTrip(outReq)
		if err != nil {
			log.Printf("[mitm] %s %s transport attempt %d (key %s): %v",
				req.Method, req.URL.Path, attempt+1, entry.masked(), err)
			if attempt == transportAttempts-1 {
				http.Error(w, "cursor-quota-proxy: upstream unreachable: "+err.Error(), http.StatusBadGateway)
				return
			}
			continue
		}
		if debugHTTP {
			log.Printf("[mitm] << %d %s (key %s)", resp.StatusCode, req.URL.Path, entry.masked())
		}
		break
	}
	if resp == nil {
		http.Error(w, "cursor-quota-proxy: upstream unreachable", http.StatusBadGateway)
		return
	}

	// --- non-200: whole-body classification ---
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
		resp.Body.Close()
		kind := classifyHTTPError(resp.StatusCode, body)
		if shouldMarkOnFailure(req.URL.Path, kind) {
			if loanBound && !loanPooled {
				s.reportPassthroughFailure(entry, kind, binding)
			} else {
				// A pooled loan rotates sticky within its allowlist eagerly, like
				// the streaming path above. Shared-pool keys keep the empty JWT:
				// they rotate lazily in Select, so Switch dwell still applies.
				rotateJWT := ""
				if loanPooled {
					rotateJWT = cliTok
				}
				s.mark(entry, kind, binding, rotateJWT, markPool())
			}
			log.Printf("[mitm] %s %s (key %s): HTTP %d classified %s - pool advanced for next request",
				req.Method, req.URL.Path, entry.masked(), resp.StatusCode, kind)
		}
		if kind == failAuth && isNonFatalAuthPath(req.URL.Path) {
			log.Printf("[mitm] %s %s (key %s): HTTP %d auth ignored (non-fatal path)",
				req.Method, req.URL.Path, entry.masked(), resp.StatusCode)
		}
		copyHeaders(w.Header(), resp.Header)
		w.WriteHeader(resp.StatusCode)
		w.Write(body)
		return
	}

	// --- 200 with Connect streaming body ---
	respCT := resp.Header.Get("Content-Type")
	if debugHTTP {
		log.Printf("[mitm] respCT=%q for %s (key %s)", respCT, req.URL.Path, entry.masked())
	}
	// onTok is shared by the Connect-envelope and SSE relay paths: it converts
	// tapped TurnEnded token counts into a Pulse usage item attributed to the
	// binding's proxy key or loan and the credential that actually served.
	onTok := func(tc TokenCounts, streamProviderModel string) {
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
			// entry.credentialID is the account that actually served this
			// turn: identical to binding.CredentialID on the pinned path,
			// and the pool-selected candidate on the roaming path.
			servedCredID := entry.credentialID
			model := coalesceBilledModel(
				logUsageModelTap(req.URL.Path, "", servedCredID, tc, body),
				streamProviderModel,
			)
			s.pulse.EnqueueUsage(UsageItem{
				LoanID:       binding.LoanID,
				CredentialID: servedCredID,
				Model:        model,
				Tokens:       tc,
			})
			return
		}
		if binding.ProxyKeyID == "" {
			return
		}
		model := coalesceBilledModel(
			logUsageModelTap(req.URL.Path, binding.ProxyKeyID, entry.credentialID, tc, body),
			streamProviderModel,
		)
		s.pulse.EnqueueUsage(UsageItem{
			ProxyKeyID:   binding.ProxyKeyID,
			CredentialID: entry.credentialID,
			Model:        model,
			Tokens:       tc,
		})
	}
	// Cursor labels some Connect server-streaming responses (notably
	// agent.v1.AgentService/RunSSE) as text/event-stream, but the body is
	// ordinary Connect envelopes — same relay and usage tap as the CLI paths.
	isEventStreamCT := strings.Contains(respCT, "text/event-stream")
	if strings.HasPrefix(respCT, "application/connect") || isEventStreamCT {
		flags, payload, err := readEnvelope(resp.Body)
		if err != nil {
			resp.Body.Close()
			log.Printf("[mitm] %s %s (key %s): stream ended before first envelope: %v",
				req.Method, req.URL.Path, entry.masked(), err)
			copyHeaders(w.Header(), resp.Header)
			w.WriteHeader(http.StatusOK)
			return
		}
		onFailure := func(kind failKind) {
			if loanBound && !loanPooled {
				s.reportPassthroughFailure(entry, kind, binding)
			} else {
				s.mark(entry, kind, binding, cliTok, markPool())
			}
			log.Printf("[mitm] %s %s (key %s): stream classified %s - pool advanced for next request",
				req.Method, req.URL.Path, entry.masked(), kind)
		}
		proxyKeyID := binding.ProxyKeyID
		credID := entry.credentialID
		if loanBound {
			proxyKeyID = ""
			credID = entry.credentialID
		}
		copyHeaders(w.Header(), resp.Header)
		w.WriteHeader(http.StatusOK)
		if err := passthroughConnectStream(w, resp.Body, flags, payload, onTok, onFailure, req.URL.Path, proxyKeyID, credID); err != nil {
			log.Printf("[mitm] %s %s (key %s): stream relay error: %v",
				req.Method, req.URL.Path, entry.masked(), err)
		}
		resp.Body.Close()
		return
	}

	// --- 200 unary: plain passthrough ---
	copyHeaders(w.Header(), resp.Header)
	w.WriteHeader(http.StatusOK)
	io.Copy(w, resp.Body)
	resp.Body.Close()
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
	var windowLimitReason string
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
		// Defer limit enforcement to business requests so agent login does not
		// collapse into the misleading "API key is invalid" warning.
		windowLimitReason = authWindowReason(res)
		log.Printf("[mitm] exchange window_limited proxy_key=%s reason=%s (enforce on request)",
			res.ProxyKeyID, windowLimitReason)
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
				Mode:                 res.Mode,
				LoanID:               res.LoanID,
				CredentialID:         res.CredentialID,
				PulseKey:             pulseKey,
				CursorAPIKey:         exchangeKey,
				WindowLimitReason:    windowLimitReason,
				AllowedCredentialIDs: res.CredentialIDs,
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
		s.sessions.Bind(token, SessionBinding{
			ProxyKeyID:         res.ProxyKeyID,
			PulseKey:           pulseKey,
			StickyCredentialID: entry.credentialID,
			WindowLimitReason:  windowLimitReason,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"accessToken":  token,
		"refreshToken": "pulse",
	})
	log.Printf("[mitm] exchange ok proxy_key=%s credential=%s", res.ProxyKeyID, entry.credentialID)
}

// ideIdentityPassthrough reports paths the IDE serves with its own login JWT
// that must never be rewritten to a pool credential. Rewriting GetMe makes the
// client's identity-consistency check fail and it re-fetches in a tight loop
// (observed: 800+ requests/min); team-scope endpoints 401 for pool credentials
// and would otherwise mark pool keys auth-bad.
func ideIdentityPassthrough(path string) bool {
	switch path {
	case "/aiserver.v1.DashboardService/GetMe",
		"/aiserver.v1.DashboardService/GetUserProfile",
		"/aiserver.v1.DashboardService/GetTeams",
		"/aiserver.v1.DashboardService/GetTeamCommands",
		"/aiserver.v1.AiService/GetUserStatus":
		return true
	}
	return false
}

// bindIDESession handles IDE-originated business requests whose bearer token
// was never issued by an intercepted exchange: Cursor IDE authenticates with
// its own WorkOS login JWT and never calls exchange_user_api_key. When an IDE
// access key is configured (-ide-pulse-key), the first request seen from a
// client token is bound to that key's pool or loan exactly like a CLI session;
// later requests reuse the binding, including session TTL re-authorize and
// sticky rotation. It writes the error response itself; handled=false means
// the caller must stop.
func (s *Server) bindIDESession(w http.ResponseWriter, cliTok string) (SessionBinding, bool) {
	if s.pulse == nil || s.idePulseKey == "" {
		http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
		return SessionBinding{}, false
	}
	res, err := s.pulse.Authorize(s.idePulseKey)
	if err != nil {
		log.Printf("[ide] authorize fail-closed: %v", err)
		http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
		return SessionBinding{}, false
	}
	windowLimitReason := ""
	switch res.Status {
	case "ok":
	case "window_limited":
		// Bind anyway; the window limit is enforced on business requests below,
		// mirroring the deferred exchange behavior so IDE clients see a clear
		// 429 resource_exhausted instead of a login failure.
		windowLimitReason = authWindowReason(res)
	case "invalid":
		http.Error(w, "invalid ide access key", http.StatusUnauthorized)
		return SessionBinding{}, false
	case "suspended":
		msg := "suspended"
		if res.Reason != nil {
			msg = *res.Reason
		}
		http.Error(w, msg, http.StatusForbidden)
		return SessionBinding{}, false
	default:
		http.Error(w, "authorize rejected", http.StatusForbidden)
		return SessionBinding{}, false
	}

	b := SessionBinding{PulseKey: s.idePulseKey, WindowLimitReason: windowLimitReason}
	if res.Mode == "loan_passthrough" || res.Mode == "loan_alias" {
		b.Mode = res.Mode
		b.LoanID = res.LoanID
		b.CredentialID = res.CredentialID
		b.AllowedCredentialIDs = res.CredentialIDs
		if key := strings.TrimSpace(res.CursorAPIKey); key != "" {
			b.CursorAPIKey = key
		}
	} else {
		if res.ProxyKeyID == "" {
			log.Printf("[ide] authorize ok but missing proxy_key_id mode=%q — refuse bind", res.Mode)
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return SessionBinding{}, false
		}
		b.ProxyKeyID = res.ProxyKeyID
	}
	if sub := jwtSub(cliTok); sub != "" {
		if !s.pinIDESub(b, sub) {
			http.Error(w, "ide access key already bound to another login", http.StatusForbidden)
			if s.pulse != nil {
				s.pulse.ReportEvent(EventItem{
					EventType:  "ide_sub_mismatch",
					ProxyKeyID: b.ProxyKeyID,
					LoanID:     b.LoanID,
					Detail:     "login identity differs from the pinned one",
				})
			}
			log.Printf("[ide] sub mismatch on key %s... — rejected", maskClientToken(s.idePulseKey))
			return SessionBinding{}, false
		}
	}
	s.sessions.Bind(cliTok, b)
	log.Printf("[ide] session bound token=%s... proxy_key_id=%s loan_id=%s credential=%s",
		maskClientToken(cliTok), res.ProxyKeyID, res.LoanID, res.CredentialID)
	return b, true
}

func maskClientToken(tok string) string {
	if len(tok) <= 12 {
		return tok
	}
	return tok[:12]
}

// pinIDESub enforces the optional login-identity lock: the first JWT sub seen
// on an IDE access key claims it; a different sub is rejected. Always allowed
// when the lock is off or the token carries no parseable sub.
func (s *Server) pinIDESub(b SessionBinding, sub string) bool {
	if !s.ideLockSub {
		return true
	}
	s.ideSubMu.Lock()
	defer s.ideSubMu.Unlock()
	if s.ideSubByKey == nil {
		s.ideSubByKey = map[string]string{}
	}
	pinned, ok := s.ideSubByKey[s.idePulseKey]
	if !ok {
		s.ideSubByKey[s.idePulseKey] = sub
		return true
	}
	return pinned == sub
}

// jwtSub best-effort extracts the "sub" claim from a JWT. IDE login tokens are
// standard JWS shapes; failure returns "" (never blocks the request).
func jwtSub(tok string) string {
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		return ""
	}
	raw := parts[1]
	if pad := len(raw) % 4; pad != 0 {
		raw += strings.Repeat("=", 4-pad)
	}
	claims, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil {
		if padded, err2 := base64.URLEncoding.DecodeString(raw); err2 == nil {
			claims = padded
		} else {
			return ""
		}
	}
	var c struct {
		Sub string `json:"sub"`
	}
	if err := json.Unmarshal(claims, &c); err != nil {
		return ""
	}
	return c.Sub
}

var debugHeaders = strings.TrimSpace(os.Getenv("PROXY_DEBUG_HEADERS")) != ""

var debugHTTP = strings.TrimSpace(os.Getenv("PROXY_DEBUG_HTTP")) != ""

func debugHeadersEnabled() bool { return debugHeaders }

func truncateForLog(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func authWindowReason(res AuthResult) string {
	if res.Reason != nil && strings.TrimSpace(*res.Reason) != "" {
		return strings.TrimSpace(*res.Reason)
	}
	return "window_limited"
}

func windowLimitMessage(reason string) string {
	switch reason {
	case "window_5h_exceeded":
		return "pulse: 5h window cost limit exceeded; raise limit or retry later"
	case "window_7d_exceeded":
		return "pulse: 7d window cost limit exceeded; raise limit or retry later"
	case "", "window_limited":
		return "pulse: window cost limit exceeded; raise limit or retry later"
	default:
		return "pulse: window cost limit exceeded (" + reason + "); raise limit or retry later"
	}
}

func writeWindowLimited(w http.ResponseWriter, reason string) {
	msg := windowLimitMessage(reason)
	log.Printf("[mitm] reject request: %s", msg)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusTooManyRequests)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"code":    "resource_exhausted",
		"message": msg,
	})
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

func (s *Server) mark(entry *keyEntry, kind failKind, binding SessionBinding, cliTok string, pool quotaPoolKind) {
	if kind == failAuth {
		s.pool.markBad(entry)
	} else {
		s.pool.markQuotaExhausted(entry, pool)
		if kind == failAccount {
			s.sticky.RotateOnExhaustion(cliTok, &binding, entry.credentialID, pool)
		}
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
