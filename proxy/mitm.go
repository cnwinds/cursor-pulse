package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

var errPoolSessionCollision = errors.New("pool session collision")

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
	if debugHeaders {
		if req.URL.Path == "/agent.v1.AgentService/RunSSE" {
			log.Printf("[hdr] %s %s ct=%q clen=%q te=%q expect=%q checksum=%q client-key=%q",
				req.Method, req.URL.Path, reqCT, req.Header.Get("Content-Length"),
				req.Header.Get("Transfer-Encoding"), req.Header.Get("Expect"),
				truncate(req.Header.Get("x-cursor-checksum"), 80),
				truncate(req.Header.Get("x-client-key"), 40))
		} else {
			log.Printf("[hdr] %s %s checksum=%q client-key=%q",
				req.Method, req.URL.Path,
				truncate(req.Header.Get("x-cursor-checksum"), 160),
				truncate(req.Header.Get("x-client-key"), 60))
		}
	}
	// skipAuth = forward the client's own Authorization untouched. Only known
	// billing RPCs are rewritten (see isBillingPath); everything else — /auth/,
	// identity RPCs, telemetry, unknown services — stays client-owned so new
	// endpoints fail open to no-attribution, never to identity breakage.
	skipAuth := !isBillingPath(req.URL.Path)
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
	rewriteAuth := !skipAuth
	// Minted CLI opaque tokens are useless upstream: rewrite /auth/* only when
	// the bearer is a bound session. Identity RPCs (GetMe etc.) stay on the
	// client's login JWT even after IDE TOFU bind — rewriting them causes the
	// IDE identity-consistency retry storm and can burn pool keys on team 401s.
	if s.sessions != nil && s.sessionTokens != nil && strings.HasPrefix(req.URL.Path, "/auth/") {
		tok := strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
		tok = strings.TrimPrefix(tok, "bearer ")
		if b, ok := s.sessions.Lookup(tok); ok {
			cliTok, binding, rewriteAuth = tok, b, true
		}
	}
	if s.sessions != nil && !skipAuth {
		cliTok = strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
		cliTok = strings.TrimPrefix(cliTok, "bearer ")
		if cliTok == "" {
			// No bearer at all must never be TOFU-bound to the IDE proxy key.
			http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
			return
		}
		ideKey := ideKeyFromCtx(req.Context())
		b, ok := s.sessions.Lookup(cliTok)
		// Per-key IDE listeners share SessionMap with the main port. A login
		// JWT previously TOFU-bound on another key's port must not keep that
		// attribution when the client moves http.proxy to this listener.
		if ok && ideKey != "" && b.PulseKey != ideKey {
			s.sessions.Delete(cliTok)
			ok = false
		}
		if !ok {
			ideB, handled := s.bindIDESession(w, req, cliTok)
			if !handled {
				return
			}
			b = ideB
		}
		if s.pulse != nil && s.sessionTTL > 0 && time.Since(b.BoundAt) > s.sessionTTL {
			// Report one slot as current and the other as held while it is
			// active: both seats stay alive, and the assignment answers for the
			// reported slot. Prefer the auto slot unless only the api slot is
			// in use (e.g. a long api stream after the auto slot went idle).
			now := time.Now()
			reauthPool := quotaPoolAuto
			if b.APISticky.CredentialID != "" && (b.AutoSticky.CredentialID == "" ||
				(!b.slotActive(quotaPoolAuto, now) && b.slotActive(quotaPoolAPI, now))) {
				reauthPool = quotaPoolAPI
			}
			current := b.sticky(reauthPool).CredentialID
			if current == "" {
				current = b.CredentialID
			}
			res, err := s.pulse.AuthorizeSeat(b.PulseKey, current, false, reauthPool, b.heldOutside(reauthPool))
			if err != nil {
				log.Printf("[mitm] session re-authorize fail-closed: %v", err)
				http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
				return
			}
			// Apply to the local copy and to the stored binding: other requests
			// on this session may have filled a slot during the Pulse call.
			store := func(apply func(*SessionBinding)) {
				apply(&b)
				if !s.sessions.Update(cliTok, apply) {
					s.sessions.Bind(cliTok, b)
				}
			}
			switch res.Status {
			case "ok":
				moved := ""
				if res.SeatAdvised && seatFollowsAssignment(b, res) {
					moved = strings.TrimSpace(res.AssignedCredentialID)
					if moved == "" {
						http.Error(w, "cursor-pulse-proxy: account concurrency limit", http.StatusServiceUnavailable)
						return
					}
				}
				store(func(x *SessionBinding) {
					x.WindowLimitReason = ""
					x.BoundAt = now
					if res.CredentialID != "" {
						x.CredentialID = res.CredentialID
					}
					if res.LoanID != "" {
						x.LoanID = res.LoanID
					}
					if res.Mode != "" {
						x.Mode = res.Mode
					}
					if strings.TrimSpace(res.CursorAPIKey) != "" {
						x.CursorAPIKey = strings.TrimSpace(res.CursorAPIKey)
					}
					// Replace (not merge): a candidate dropped from the ranked
					// allowlist must stop serving on the next request. Empty clears
					// the scope, which returns the binding to the pinned path.
					x.AllowedCredentialIDs = res.CredentialIDs
					if res.SeatAdvised {
						x.BlockedCredentialIDs = res.BlockedCredentialIDs
					}
					if moved != "" && moved != current {
						*x.sticky(reauthPool) = stickySlot{CredentialID: moved, Since: now}
					}
				})
			case "window_limited":
				reason := authWindowReason(res)
				store(func(x *SessionBinding) {
					x.WindowLimitReason = reason
					x.BoundAt = now
				})
				writeWindowLimited(w, reason)
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
	tracksLoan := attributesUsageToLoan(binding)
	// A loan_alias binding carrying a Pulse-issued candidate allowlist selects
	// among those accounts exactly like the shared pool (sticky + Switch dwell +
	// per-bucket availability) instead of being pinned to one credential. An
	// allowlist that collapses to nil (empty, or all-blank entries) keeps the
	// legacy passthrough path — the same predicate sticky.Select uses.
	loanPooled := binding.Mode == "loan_alias" && s.sticky != nil && binding.allowedSet() != nil
	quotaPool := resolveQuotaPool(req.Context(), req.URL.Path, reqBodySnap, streamFS)
	if (binding.Mode == "loan_alias" || binding.Mode == "loan_pool") &&
		strings.Contains(req.URL.Path, "AgentService/Run") && s.pulse != nil {
		model := findModelName(reqBodySnap())
		capRes, err := s.pulse.CheckLoanUsageCap(binding.LoanID, model)
		if err != nil {
			log.Printf("[mitm] loan usage cap check failed loan=%s: %v", binding.LoanID, err)
			http.Error(w, "cursor-pulse-proxy: 借用用量校验暂不可用，请稍后重试", http.StatusServiceUnavailable)
			return
		}
		if capRes.Status == "limited" {
			writeLoanUsageCapLimited(w, capRes.Message)
			return
		}
	}
	markPool := func() quotaPoolKind {
		return effectiveMarkQuotaPool(req.URL.Path, reqBodySnap, quotaPool)
	}
	transportAttempts := 3
	if loanBound && !loanPooled {
		transportAttempts = 1
	}

	var entry *keyEntry
	var resp *http.Response
	if skipAuth && !rewriteAuth {
		// Passthrough (identity RPCs / telemetry / unknown families, and the
		// /auth/ family when the bearer is not a bound opaque session token):
		// forward the client's own Authorization untouched, with no credential
		// selection and no pool marking — a failure here is the client's,
		// never an account failure that should rotate the pool.
		outReq, err := http.NewRequestWithContext(req.Context(), req.Method, target, bodyFor())
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		copyHeaders(outReq.Header, req.Header)
		outReq.Header.Del("Accept-Encoding")
		resp, err = s.transport.RoundTrip(outReq)
		if err != nil {
			log.Printf("[mitm] %s %s passthrough: %v", req.Method, req.URL.Path, err)
			http.Error(w, "cursor-quota-proxy: upstream unreachable: "+err.Error(), http.StatusBadGateway)
			return
		}
	} else {
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
				if tracksLoan {
					ev.LoanID = binding.LoanID
					ev.CredentialID = binding.CredentialID
				} else {
					ev.ProxyKeyID = binding.ProxyKeyID
				}
				s.pulse.ReportEvent(ev)
			}
			msg := "cursor-quota-proxy: all API keys exhausted"
			if loanBound || binding.Mode == "loan_pool" {
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
		if rewriteAuth {
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
		break
	}
	}
	if resp == nil {
		http.Error(w, "cursor-quota-proxy: upstream unreachable", http.StatusBadGateway)
		return
	}
	if loanPooled || (!loanBound && s.sticky != nil) {
		defer s.sticky.Track(cliTok, quotaPool)()
	}

	// --- non-200: whole-body classification ---
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
		resp.Body.Close()
		kind := classifyHTTPError(resp.StatusCode, body)
		if entry != nil && shouldMarkOnFailure(req.URL.Path, kind) {
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
		copyHeaders(w.Header(), resp.Header)
		w.WriteHeader(resp.StatusCode)
		w.Write(body)
		return
	}

	// --- 200 with Connect streaming body ---
	respCT := resp.Header.Get("Content-Type")
	if debugHTTP {
		log.Printf("[mitm] respCT=%q for %s (key %s)", respCT, req.URL.Path, maskedEntry(entry))
	}
	// Cursor labels some Connect server-streaming responses (notably
	// agent.v1.AgentService/RunSSE) as text/event-stream, but the body is
	// ordinary Connect envelopes — same relay and usage tap as the CLI paths.
	// Gate by service prefix so a genuinely SSE-framed endpoint outside the
	// Connect services keeps the legacy unary passthrough instead of being
	// truncated by a failed envelope read.
	isEventStreamCT := strings.Contains(respCT, "text/event-stream") && isCursorRPCFamily(req.URL.Path)
	if strings.HasPrefix(respCT, "application/connect") || isEventStreamCT {
		flags, payload, err := readEnvelope(resp.Body)
		if err != nil {
			resp.Body.Close()
			log.Printf("[mitm] %s %s (key %s): stream ended before first envelope: %v",
				req.Method, req.URL.Path, entry.masked(), err)
			// Do not echo a silent 200 empty body — IDE chat would hang with no
			// retry signal and usage would never be recorded.
			http.Error(w, "cursor-quota-proxy: upstream stream ended before first envelope", http.StatusBadGateway)
			return
		}
		onTok := func(tc TokenCounts, streamProviderModel string) {
			if s.pulse == nil {
				return
			}
			var body []byte
			if reqBodySnap != nil {
				body = reqBodySnap()
			}
			if tracksLoan {
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
		credID := ""
		if entry != nil {
			credID = entry.credentialID
		}
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
	// Exchange carries no model: the first account comes from the auto order.
	res, err := s.pulse.AuthorizeSeat(pulseKey, "", false, quotaPoolAuto, nil)
	if err != nil {
		log.Printf("[mitm] authorize fail-closed: %v", err)
		http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
		return
	}
	var windowLimitReason string
	switch res.Status {
	case "ok":
		if assignmentMissing(res) {
			log.Printf("[mitm] concurrency cap mode=%s loan_id=%s proxy_key=%s", res.Mode, res.LoanID, res.ProxyKeyID)
			http.Error(w, "cursor-pulse-proxy: account concurrency limit", http.StatusServiceUnavailable)
			return
		}
	case "window_limited":
		// Defer limit enforcement to business requests so agent login does not
		// collapse into the misleading "API key is invalid" warning.
		windowLimitReason = authWindowReason(res)
		log.Printf("[mitm] exchange window_limited proxy_key=%s reason=%s (enforce on request)",
			res.ProxyKeyID, windowLimitReason)
	default:
		writeAuthReject(w, res)
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
		clientTok, expiresAt := s.clientSessionToken(token)
		if s.sessions != nil {
			binding := SessionBinding{
				Mode:                 res.Mode,
				LoanID:               res.LoanID,
				CredentialID:         res.CredentialID,
				PulseKey:             pulseKey,
				CursorAPIKey:         exchangeKey,
				WindowLimitReason:    windowLimitReason,
				AllowedCredentialIDs: res.CredentialIDs,
				ExpiresAt:            expiresAt,
			}
			if res.SeatAdvised {
				binding.BlockedCredentialIDs = res.BlockedCredentialIDs
				assigned := strings.TrimSpace(res.AssignedCredentialID)
				if assigned != "" && containsID(res.CredentialIDs, assigned) {
					binding.AutoSticky = stickySlot{CredentialID: assigned, Since: time.Now()}
				}
			}
			s.sessions.Bind(clientTok, binding)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  clientTok,
			"refreshToken": "pulse",
		})
		log.Printf("[mitm] exchange ok %s loan_id=%s credential=%s", res.Mode, res.LoanID, res.CredentialID)
		return
	}

	if res.Mode == "loan_pool" {
		if res.LoanID == "" {
			log.Printf("[mitm] loan_pool missing loan_id")
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return
		}
		entry, token, err := s.exchangeFromPool(req.Context(), &res, pulseKey)
		if err != nil {
			if errors.Is(err, errPoolSessionCollision) {
				http.Error(w, "cursor-pulse-proxy: unable to mint unique session token", http.StatusServiceUnavailable)
				return
			}
			s.reportPoolExhausted(res, err.Error())
			http.Error(w, "cursor-pulse-proxy: all API keys exhausted", http.StatusServiceUnavailable)
			return
		}
		clientTok, expiresAt := s.clientSessionToken(token)
		if s.sessions != nil {
			s.sessions.Bind(clientTok, SessionBinding{
				Mode:                 res.Mode,
				LoanID:               res.LoanID,
				PulseKey:             pulseKey,
				AutoSticky:           stickySlot{CredentialID: entry.credentialID, Since: time.Now()},
				WindowLimitReason:    windowLimitReason,
				BlockedCredentialIDs: res.BlockedCredentialIDs,
				ExpiresAt:            expiresAt,
			})
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  clientTok,
			"refreshToken": "pulse",
		})
		log.Printf("[mitm] exchange ok loan_pool loan_id=%s credential=%s", res.LoanID, entry.credentialID)
		return
	}

	if res.ProxyKeyID == "" {
		log.Printf("[mitm] authorize ok but missing proxy_key_id mode=%q — refuse pool path", res.Mode)
		http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
		return
	}

	entry, token, err := s.exchangeFromPool(req.Context(), &res, pulseKey)
	if err != nil {
		if errors.Is(err, errPoolSessionCollision) {
			log.Printf("[mitm] exchange jwt collision unresolved proxy_key=%s", res.ProxyKeyID)
			http.Error(w, "cursor-pulse-proxy: unable to mint unique session token", http.StatusServiceUnavailable)
			return
		}
		s.reportPoolExhausted(res, err.Error())
		http.Error(w, "cursor-pulse-proxy: all API keys exhausted", http.StatusServiceUnavailable)
		return
	}
	clientTok, expiresAt := s.clientSessionToken(token)
	if s.sessions != nil {
		s.sessions.Bind(clientTok, SessionBinding{
			ProxyKeyID:           res.ProxyKeyID,
			PulseKey:             pulseKey,
			AutoSticky:           stickySlot{CredentialID: entry.credentialID, Since: time.Now()},
			WindowLimitReason:    windowLimitReason,
			BlockedCredentialIDs: res.BlockedCredentialIDs,
			ExpiresAt:            expiresAt,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"accessToken":  clientTok,
		"refreshToken": "pulse",
	})
	log.Printf("[mitm] exchange ok proxy_key=%s credential=%s", res.ProxyKeyID, entry.credentialID)
}

func attributesUsageToLoan(b SessionBinding) bool {
	switch b.Mode {
	case "loan_passthrough", "loan_alias", "loan_pool":
		return b.LoanID != ""
	default:
		return false
	}
}

// exchangeConflicts reports whether an existing session JWT cannot be reused
// for this authorize result. loan_pool borrowers share an empty ProxyKeyID, so
// identity is the loan id; a pk_ holder and a different loan both conflict.
// Sessions are keyed by minted client tokens unless PROXY_OPAQUE_SESSION_TOKEN
// is off, so an upstream JWT only ever collides in that legacy mode.
func exchangeConflicts(existing SessionBinding, mode, proxyKeyID, loanID string) bool {
	if existing.ProxyKeyID == "" && existing.LoanID == "" {
		return false
	}
	if mode == "loan_pool" {
		return existing.LoanID != loanID || existing.ProxyKeyID != ""
	}
	if proxyKeyID != "" && existing.ProxyKeyID == proxyKeyID && existing.LoanID == "" {
		return false
	}
	return existing.ProxyKeyID != "" || existing.LoanID != ""
}

func (s *Server) reportPoolExhausted(res AuthResult, detail string) {
	if s.pulse == nil {
		return
	}
	ev := EventItem{EventType: "exhausted", Detail: detail}
	if res.Mode == "loan_pool" {
		ev.LoanID = res.LoanID
	} else {
		ev.ProxyKeyID = res.ProxyKeyID
	}
	s.pulse.ReportEvent(ev)
}

// exchangeFromPool mints a pool JWT that is not already bound to a different
// borrower or proxy key. Same loan / same proxy key may re-bind.
func (s *Server) exchangeFromPool(ctx context.Context, res *AuthResult, pulseKey string) (*keyEntry, string, error) {
	if res != nil && res.SeatAdvised {
		return s.exchangeAdvised(ctx, res, pulseKey)
	}
	base := AuthResult{}
	if res != nil {
		base = *res
	}
	return s.exchangeUnadvised(ctx, base, nil)
}

func (s *Server) exchangeAdvised(ctx context.Context, res *AuthResult, pulseKey string) (*keyEntry, string, error) {
	current := strings.TrimSpace(res.AssignedCredentialID)
	if current == "" {
		return nil, "", errAllExhausted
	}
	seen := map[string]bool{}
	maxTries := s.pool.size()
	if maxTries < 1 {
		maxTries = 1
	}
	for tries := 0; tries < maxTries; tries++ {
		if current == "" || seen[current] {
			return nil, "", errAllExhausted
		}
		seen[current] = true
		entry, token, err := s.pool.tokenForCredential(ctx, current)
		if err != nil && !errors.Is(err, errAllExhausted) && !isPermanentExchangeErr(err) {
			return entry, "", err
		}
		collided := false
		if err == nil && s.sessions != nil {
			if b, ok := s.sessions.Lookup(token); ok && exchangeConflicts(b, res.Mode, res.ProxyKeyID, res.LoanID) {
				collided = true
				log.Printf("[mitm] exchange jwt collision mode=%s proxy_key=%s loan_id=%s held_proxy=%s held_loan=%s skip_credential=%s",
					res.Mode, res.ProxyKeyID, res.LoanID, b.ProxyKeyID, b.LoanID, entry.credentialID)
			}
		}
		if err == nil && !collided {
			res.AssignedCredentialID = entry.credentialID
			return entry, token, nil
		}
		if s.pulse == nil {
			return s.exchangeUnadvised(ctx, *res, seen)
		}
		next, err := s.pulse.AuthorizeSeat(pulseKey, current, true, quotaPoolAuto, nil)
		if err != nil || !next.SeatAdvised {
			log.Printf("[mitm] seat reassignment unavailable: %v", err)
			return s.exchangeUnadvised(ctx, *res, seen)
		}
		res.BlockedCredentialIDs = next.BlockedCredentialIDs
		current = strings.TrimSpace(next.AssignedCredentialID)
	}
	return nil, "", errPoolSessionCollision
}

func (s *Server) exchangeUnadvised(ctx context.Context, res AuthResult, skip map[string]bool) (*keyEntry, string, error) {
	var entry *keyEntry
	var token string
	var err error
	if len(skip) == 0 {
		entry, token, err = s.pool.token(ctx)
	} else {
		entry, token, err = s.pool.tokenSkipping(ctx, skip)
	}
	if err != nil {
		return nil, "", err
	}
	if s.sessions == nil {
		return entry, token, nil
	}
	if skip == nil {
		skip = map[string]bool{}
	}
	maxTries := s.pool.size()
	if maxTries < 1 {
		maxTries = 1
	}
	for tries := 0; tries < maxTries; tries++ {
		b, ok := s.sessions.Lookup(token)
		if !ok || !exchangeConflicts(b, res.Mode, res.ProxyKeyID, res.LoanID) {
			return entry, token, nil
		}
		skip[entry.credentialID] = true
		log.Printf("[mitm] exchange jwt collision mode=%s proxy_key=%s loan_id=%s held_proxy=%s held_loan=%s skip_credential=%s",
			res.Mode, res.ProxyKeyID, res.LoanID, b.ProxyKeyID, b.LoanID, entry.credentialID)
		entry, token, err = s.pool.tokenSkipping(ctx, skip)
		if err != nil {
			return nil, "", err
		}
	}
	if b, ok := s.sessions.Lookup(token); ok && exchangeConflicts(b, res.Mode, res.ProxyKeyID, res.LoanID) {
		return nil, "", errPoolSessionCollision
	}
	return entry, token, nil
}

func assignmentMissing(res AuthResult) bool {
	if !res.SeatAdvised || strings.TrimSpace(res.AssignedCredentialID) != "" {
		return false
	}
	return seatFollowsAssignment(SessionBinding{
		Mode:       res.Mode,
		ProxyKeyID: res.ProxyKeyID,
	}, res)
}

func seatFollowsAssignment(b SessionBinding, res AuthResult) bool {
	if b.Mode == "loan_pool" {
		return true
	}
	if b.Mode == "loan_alias" && len(res.CredentialIDs) > 0 {
		return true
	}
	if b.Mode != "loan_passthrough" && b.Mode != "loan_alias" && b.ProxyKeyID != "" {
		return true
	}
	return false
}

func containsID(ids []string, want string) bool {
	for _, id := range ids {
		if id == want {
			return true
		}
	}
	return false
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

func writeLoanUsageCapLimited(w http.ResponseWriter, message string) {
	log.Printf("[mitm] reject request: loan usage cap: %s", truncate(message, 200))
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusTooManyRequests)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"code":    "resource_exhausted",
		"message": message,
	})
}

func (s *Server) reportPassthroughFailure(entry *keyEntry, kind failKind, binding SessionBinding) {
	if entry == nil {
		return
	}
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
	if entry == nil {
		return
	}
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
			LoanID:       binding.LoanID,
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

// PROXY_DEBUG_HEADERS / PROXY_DEBUG_HTTP enable verbose request logging
// (off by default).
var (
	debugHeaders = strings.TrimSpace(os.Getenv("PROXY_DEBUG_HEADERS")) != ""
	debugHTTP    = strings.TrimSpace(os.Getenv("PROXY_DEBUG_HTTP")) != ""
)

// maskedEntry is nil-safe log labeling for passthrough requests (no pool
// credential was selected).
func maskedEntry(e *keyEntry) string {
	if e == nil {
		return "passthrough"
	}
	return e.masked()
}

func maskClientToken(tok string) string {
	if len(tok) <= 12 {
		return tok
	}
	return tok[:12]
}

// ideKeyCtx carries the per-key IDE listener's proxy key through the CONNECT
// hop into the MITM'd requests, so the Server itself is never cloned (every
// field, lock, and cache stays shared by construction).
type ideKeyCtx struct{}

func withIDEKeyCtx(ctx context.Context, key string) context.Context {
	return context.WithValue(ctx, ideKeyCtx{}, key)
}

func ideKeyFromCtx(ctx context.Context) string {
	k, _ := ctx.Value(ideKeyCtx{}).(string)
	return k
}

// isCursorRPCFamily reports the Cursor AI RPC families the proxy understands.
// It gates both auth rewriting and the event-stream envelope routing, so the
// two stay in lockstep.
func isCursorRPCFamily(path string) bool {
	return strings.HasPrefix(path, "/agent.v1.") || strings.HasPrefix(path, "/aiserver.v1.")
}

// isBillingPath reports paths whose Authorization must be rewritten to a pool
// credential. Everything else — the /auth/ family, the identity RPCs below,
// telemetry, and future services — keeps the client's own token: an unknown
// path fails open to no-attribution instead of breaking identity consistency
// (observed: rewriting GetMe made the IDE re-fetch in a tight loop).
func isBillingPath(path string) bool {
	return isCursorRPCFamily(path) && !ideIdentityPassthrough(path)
}

// ideIdentityPassthrough reports RPC-family paths the IDE serves with its own
// login JWT that must never be rewritten to a pool credential (team-scope
// endpoints also 401 for pool credentials and would burn pool keys).
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

// ideSubStore is the login-identity lock state shared by the main port and
// every per-key IDE listener: enabled flag plus the per-proxy-key pinned subs.
type ideSubStore struct {
	mu      sync.Mutex
	enabled bool
	byKey   map[string]string // proxy key → pinned login sub
}

// pin records/validates the login sub for a proxy key. A nil or disabled store
// allows everything; the first sub claims the key, a different sub loses.
func (s *ideSubStore) pin(key, sub string) bool {
	if s == nil || !s.enabled {
		return true
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.byKey == nil {
		s.byKey = map[string]string{}
	}
	if pinned, ok := s.byKey[key]; ok {
		return pinned == sub
	}
	s.byKey[key] = sub
	return true
}

// ideLockSubInit wires the optional login-identity lock (PROXY_IDE_LOCK_SUB).
// The store is created before any per-key listener can be cloned so every
// listener shares one view.
func (s *Server) ideLockSubInit(enabled bool) {
	if !enabled {
		return
	}
	s.ideSub = &ideSubStore{enabled: true}
	log.Printf("IDE sub lock: each proxy key pins to the first login identity (PROXY_IDE_LOCK_SUB)")
}

// authorizeOrReject maps a plain authorize call to the shared HTTP semantics:
// "ok"/"window_limited" proceed (window limits are enforced on business
// requests, not at login); everything else writes the error response itself
// and reports handled=false. Seat-aware exchange keeps its own flow
// (AuthorizeSeat + assignmentMissing) in handleExchange.
func authorizeOrReject(w http.ResponseWriter, pulse *PulseClient, key string) (AuthResult, bool) {
	return authorizeOrRejectMode(w, pulse, key, false)
}

// authorizeOrRejectFresh bypasses the auth TTL cache so revoke/suspend is
// visible immediately on IDE /ide-port and TOFU bind paths.
func authorizeOrRejectFresh(w http.ResponseWriter, pulse *PulseClient, key string) (AuthResult, bool) {
	return authorizeOrRejectMode(w, pulse, key, true)
}

func authorizeOrRejectMode(w http.ResponseWriter, pulse *PulseClient, key string, fresh bool) (AuthResult, bool) {
	var (
		res AuthResult
		err error
	)
	if fresh {
		res, err = pulse.AuthorizeFresh(key)
	} else {
		res, err = pulse.Authorize(key)
	}
	if err != nil {
		log.Printf("[mitm] authorize fail-closed: %v", err)
		http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
		return res, false
	}
	if res.Status == "ok" || res.Status == "window_limited" {
		return res, true
	}
	writeAuthReject(w, res)
	return res, false
}

// writeAuthReject maps the rejecting authorize statuses to their shared HTTP
// responses (used by both the plain and seat-aware flows).
func writeAuthReject(w http.ResponseWriter, res AuthResult) {
	switch res.Status {
	case "invalid":
		http.Error(w, "invalid pulse key", http.StatusUnauthorized)
	case "suspended":
		msg := "suspended"
		if res.Reason != nil {
			msg = *res.Reason
		}
		http.Error(w, msg, http.StatusForbidden)
	default:
		http.Error(w, "authorize rejected", http.StatusForbidden)
	}
}

// bindIDESession handles IDE-originated business requests whose bearer token
// was never issued by an intercepted exchange: Cursor IDE authenticates with
// its own WorkOS login JWT and never calls exchange_user_api_key. When an IDE
// proxy key is configured (-ide-pulse-key / per-key port), the first request
// seen from a client token is bound to that key's pool or loan exactly like a
// CLI session; later requests reuse the binding, including session TTL
// re-authorize and sticky rotation. It writes the error response itself;
// handled=false means the caller must stop.
func (s *Server) bindIDESession(w http.ResponseWriter, req *http.Request, cliTok string) (SessionBinding, bool) {
	// Request-scoped key: the per-key listener's context key wins, else the
	// server-wide -ide-pulse-key fallback (main port).
	ideKey := ideKeyFromCtx(req.Context())
	if ideKey == "" {
		ideKey = s.idePulseKey
	}
	if s.pulse == nil || ideKey == "" {
		http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
		return SessionBinding{}, false
	}
	res, ok := authorizeOrRejectFresh(w, s.pulse, ideKey)
	if !ok {
		return SessionBinding{}, false
	}
	// window_limited still binds: the limit is enforced on business requests
	// below, mirroring the deferred exchange behavior so IDE clients see a
	// clear 429 resource_exhausted instead of a login failure.
	windowLimitReason := ""
	if res.Status == "window_limited" {
		windowLimitReason = authWindowReason(res)
	}

	b := SessionBinding{PulseKey: ideKey, WindowLimitReason: windowLimitReason}
	if res.Mode == "loan_passthrough" || res.Mode == "loan_alias" {
		b.Mode = res.Mode
		b.LoanID = res.LoanID
		b.CredentialID = res.CredentialID
		b.AllowedCredentialIDs = res.CredentialIDs
		if key := strings.TrimSpace(res.CursorAPIKey); key != "" {
			b.CursorAPIKey = key
		}
	} else if res.Mode == "loan_pool" {
		// Pool-roaming loan key (routing_mode=pool): no exchange to pin a
		// seat here, so bind LoanID/PulseKey and let the first business
		// request select via sticky + the seat advisor, exactly like the
		// exchange path presets AutoSticky for CLI sessions.
		if res.LoanID == "" {
			log.Printf("[ide] loan_pool missing loan_id — refuse bind")
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return SessionBinding{}, false
		}
		b.Mode = res.Mode
		b.LoanID = res.LoanID
		b.CredentialID = res.CredentialID
		if res.SeatAdvised {
			b.BlockedCredentialIDs = res.BlockedCredentialIDs
		}
	} else {
		if res.ProxyKeyID == "" {
			log.Printf("[ide] authorize ok but missing proxy_key_id mode=%q — refuse bind", res.Mode)
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return SessionBinding{}, false
		}
		b.ProxyKeyID = res.ProxyKeyID
	}
	if s.ideSub != nil && s.ideSub.enabled {
		// Fail closed: an unparseable / sub-less bearer must not claim the key
		// (and later lock out a real login), nor skip the pin check entirely.
		sub := jwtSub(cliTok)
		if sub == "" {
			http.Error(w, "login identity required", http.StatusForbidden)
			log.Printf("[ide] lock on but no JWT sub on key %s — rejected", maskClientToken(ideKey))
			return SessionBinding{}, false
		}
		if !s.ideSub.pin(ideKey, sub) {
			http.Error(w, "proxy key already bound to another login", http.StatusForbidden)
			if s.pulse != nil {
				s.pulse.ReportEvent(EventItem{
					EventType:  "ide_sub_mismatch",
					ProxyKeyID: b.ProxyKeyID,
					LoanID:     b.LoanID,
					Detail:     "login identity differs from the pinned one",
				})
			}
			log.Printf("[ide] sub mismatch on key %s — rejected", maskClientToken(ideKey))
			return SessionBinding{}, false
		}
	}
	s.sessions.Bind(cliTok, b)
	log.Printf("[ide] session bound token=%s... proxy_key_id=%s loan_id=%s credential=%s",
		maskClientToken(cliTok), res.ProxyKeyID, res.LoanID, res.CredentialID)
	return b, true
}

// jwtSub best-effort extracts the "sub" claim from a JWT. IDE login tokens are
// standard JWS shapes; failure returns "". Callers decide whether empty blocks
// (PROXY_IDE_LOCK_SUB fail-closed) or is ignored (lock off).
//
// This does NOT verify the JWS signature (no WorkOS JWKS in-process). It does
// reject alg=none / empty alg and control characters in sub so a trivial forged
// token cannot claim a key under PROXY_IDE_LOCK_SUB. Full signature verify remains
// a known trust-boundary limitation shared with CLI TOFU on reachable ports.
func jwtSub(tok string) string {
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		return ""
	}
	decode := func(seg string) ([]byte, bool) {
		raw := seg
		if pad := len(raw) % 4; pad != 0 {
			raw += strings.Repeat("=", 4-pad)
		}
		if b, err := base64.RawURLEncoding.DecodeString(seg); err == nil {
			return b, true
		}
		if b, err := base64.URLEncoding.DecodeString(raw); err == nil {
			return b, true
		}
		return nil, false
	}
	hdrBytes, ok := decode(parts[0])
	if !ok {
		return ""
	}
	var hdr struct {
		Alg string `json:"alg"`
	}
	if err := json.Unmarshal(hdrBytes, &hdr); err != nil {
		return ""
	}
	alg := strings.ToUpper(strings.TrimSpace(hdr.Alg))
	if alg == "" || alg == "NONE" {
		return ""
	}
	claims, ok := decode(parts[1])
	if !ok {
		return ""
	}
	var c struct {
		Sub string `json:"sub"`
	}
	if err := json.Unmarshal(claims, &c); err != nil {
		return ""
	}
	sub := c.Sub
	if sub == "" || len(sub) > 256 {
		return ""
	}
	for _, r := range sub {
		if r < 0x20 || r == 0x7f {
			return ""
		}
	}
	return sub
}
