package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

var errAllExhausted = errors.New("all API keys exhausted")

const (
	exchangePath           = "/auth/exchange_user_api_key"
	exchangeTimeout        = 15 * time.Second
	authBadCooldown          = 2 * time.Minute
)

// exchangeHTTPError is returned for non-2xx responses from Cursor's exchange.
type exchangeHTTPError struct {
	status int
	body   string
}

func (e *exchangeHTTPError) Error() string {
	return fmt.Sprintf("exchange failed: HTTP %d: %s", e.status, truncate(e.body, 200))
}

func (e *exchangeHTTPError) permanent() bool {
	return e.status == http.StatusUnauthorized || e.status == http.StatusForbidden
}

func isPermanentExchangeErr(err error) bool {
	var he *exchangeHTTPError
	return errors.As(err, &he) && he.permanent()
}

type keyEntry struct {
	credentialID string
	apiKey       string

	mu        sync.Mutex
	jwt       string
	exp       time.Time
	exhausted bool      // quota exhausted — kept across Pulse hot-updates
	badUntil  time.Time // auth/exchange failure cooldown — cleared on hot-update / after TTL
}

func (e *keyEntry) id() string {
	if e.credentialID != "" {
		return e.credentialID
	}
	return e.masked()
}

func (e *keyEntry) masked() string {
	k := e.apiKey
	if len(k) <= 10 {
		return k[:2] + "..."
	}
	return k[:6] + "..." + k[len(k)-4:]
}

// unavailable reports whether the key should be skipped (quota or auth cooldown).
func (e *keyEntry) unavailable() bool {
	return e.exhausted || (!e.badUntil.IsZero() && time.Now().Before(e.badUntil))
}

// invalidate clears the cached JWT so the next ensureToken re-exchanges.
func (e *keyEntry) invalidate() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.jwt = ""
	e.exp = time.Time{}
}

func exchangeCursorAPIKey(ctx context.Context, client *http.Client, exchangeBase, apiKey string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, exchangeBase+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return "", &exchangeHTTPError{status: resp.StatusCode, body: string(body)}
	}
	var out struct {
		AccessToken string `json:"accessToken"`
	}
	if err := json.Unmarshal(body, &out); err != nil || out.AccessToken == "" {
		return "", fmt.Errorf("exchange: bad response: %s", truncate(string(body), 200))
	}
	return out.AccessToken, nil
}

// ensureToken returns a cached JWT, minting a fresh one via the exchange
// endpoint when missing or expiring within 4 minutes.
func (e *keyEntry) ensureToken(ctx context.Context, client *http.Client, exchangeBase string) (string, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.jwt != "" && time.Until(e.exp) > 4*time.Minute {
		return e.jwt, nil
	}
	tok, err := exchangeCursorAPIKey(ctx, client, exchangeBase, e.apiKey)
	if err != nil {
		return "", err
	}
	exp, err := jwtExpiry(tok)
	if err != nil {
		exp = time.Now().Add(30 * time.Minute)
	}
	e.jwt, e.exp = tok, exp
	return e.jwt, nil
}

type Pool struct {
	mu   sync.Mutex
	keys []*keyEntry
	cur  int

	client       *http.Client
	exchangeBase string
}

func NewPool(keys []string) *Pool {
	creds := make([]PoolCredential, 0, len(keys))
	for i, k := range keys {
		creds = append(creds, PoolCredential{CredentialID: fmt.Sprintf("local-%d", i), APIKey: k})
	}
	return NewPoolFromCredentials(creds)
}

func NewPoolFromCredentials(creds []PoolCredential) *Pool {
	p := &Pool{
		// Direct by default — never inherit HTTPS_PROXY (would loop into ourselves).
		client: &http.Client{
			Timeout:   20 * time.Second,
			Transport: newOutboundTransport(nil),
		},
		exchangeBase: "https://api2.cursor.sh",
	}
	for _, c := range creds {
		p.keys = append(p.keys, &keyEntry{credentialID: c.CredentialID, apiKey: c.APIKey})
	}
	return p
}

// SetUpstreamProxy routes pool exchange traffic via the given HTTP(S) proxy.
// Pass nil to force direct (no env proxy inheritance).
func (p *Pool) SetUpstreamProxy(upstream *url.URL) {
	p.client.Transport = newOutboundTransport(upstream)
}

// ReplaceFromPulse merges Pulse pool credentials into the live pool.
// Same credential_id keeps quota exhaustion + cached JWT; auth-bad cooldown is
// cleared so keys can be retried after Pulse reconnects. Removed ids are dropped;
// new ids are appended. Cursor position is reset.
func (p *Pool) ReplaceFromPulse(creds []PoolCredential) {
	p.mu.Lock()
	defer p.mu.Unlock()
	byID := map[string]*keyEntry{}
	for _, e := range p.keys {
		byID[e.credentialID] = e
	}
	var next []*keyEntry
	for _, c := range creds {
		if c.CredentialID == "" || c.APIKey == "" {
			continue
		}
		if old, ok := byID[c.CredentialID]; ok {
			old.apiKey = c.APIKey
			old.badUntil = time.Time{} // allow retry after Pulse/pool refresh
			next = append(next, old)
			continue
		}
		next = append(next, &keyEntry{credentialID: c.CredentialID, apiKey: c.APIKey})
	}
	p.keys = next
	p.cur = 0
	log.Printf("[pool] hot-updated: %d credential(s)", len(p.keys))
}

func (p *Pool) size() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.keys)
}

// token returns a usable JWT for the current key, minting or rotating as
// needed. Returns errAllExhausted when every key is marked exhausted/bad.
func (p *Pool) token(ctx context.Context) (*keyEntry, string, error) {
	return p.tokenSkipping(ctx, nil)
}

// tokenSkipping walks the pool like token but skips credential IDs in skipCredIDs.
// Skips are temporary for this call only (not permanent exhaustion).
func (p *Pool) tokenSkipping(ctx context.Context, skipCredIDs map[string]bool) (*keyEntry, string, error) {
	p.mu.Lock()
	keys := append([]*keyEntry(nil), p.keys...)
	start := p.cur
	p.mu.Unlock()

	n := len(keys)
	if n == 0 {
		return nil, "", errAllExhausted
	}
	for i := 0; i < n; i++ {
		// Client gone → stop without burning the pool.
		if err := ctx.Err(); err != nil {
			return nil, "", err
		}

		e := keys[(start+i)%n]

		p.mu.Lock()
		skip := e.unavailable()
		if !skip {
			for j, k := range p.keys {
				if k == e {
					p.cur = j
					break
				}
			}
		}
		p.mu.Unlock()
		if skip {
			continue
		}
		if skipCredIDs[e.credentialID] {
			continue
		}

		// Detach from client ctx so agent disconnect does not cancel upstream exchange
		// mid-flight and cascade-mark every key bad.
		exchCtx, cancel := context.WithTimeout(context.Background(), exchangeTimeout)
		tok, err := e.ensureToken(exchCtx, p.client, p.exchangeBase)
		cancel()
		if err != nil {
			if isPermanentExchangeErr(err) {
				log.Printf("[pool] key %s exchange failed: %v - marking bad", e.masked(), err)
				p.markBad(e)
				continue
			}
			log.Printf("[pool] key %s exchange failed (transient): %v - not marking bad", e.masked(), err)
			// Transient failure on this key: try next without permanent blacklist.
			continue
		}
		return e, tok, nil
	}
	return nil, "", errAllExhausted
}

func (p *Pool) current() *keyEntry {
	p.mu.Lock()
	defer p.mu.Unlock()
	n := len(p.keys)
	for i := 0; i < n; i++ {
		e := p.keys[(p.cur+i)%n]
		if !e.unavailable() {
			p.cur = (p.cur + i) % n
			return e
		}
	}
	return nil
}

// markExhausted marks e as quota-exhausted and advances the cursor.
func (p *Pool) markExhausted(e *keyEntry) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !e.exhausted {
		e.exhausted = true
		log.Printf("[pool] key %s marked exhausted (quota)", e.masked())
	}
	p.advanceLocked()
}

// markBad puts e into a short auth-failure cooldown (not permanent). Cleared by
// Pulse pool hot-update or when the cooldown expires — avoids sticky
// "all keys exhausted" after transient Pulse/Cursor outages.
func (p *Pool) markBad(e *keyEntry) {
	p.mu.Lock()
	e.badUntil = time.Now().Add(authBadCooldown)
	log.Printf("[pool] key %s marked bad until %s (auth/exchange failure)",
		e.masked(), e.badUntil.Format(time.RFC3339))
	p.advanceLocked()
	p.mu.Unlock()
	e.invalidate()
}

func (p *Pool) advanceLocked() {
	n := len(p.keys)
	for i := 1; i <= n; i++ {
		next := p.keys[(p.cur+i)%n]
		if !next.unavailable() {
			p.cur = (p.cur + i) % n
			log.Printf("[pool] rotated to key %s (%d/%d)", next.masked(), (p.cur+i)%n+1, n)
			return
		}
	}
}

// reset clears exhaustion and auth-bad flags (e.g. on manual signal / new day).
func (p *Pool) reset() {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, e := range p.keys {
		e.exhausted = false
		e.badUntil = time.Time{}
	}
	p.cur = 0
	log.Printf("[pool] exhaustion flags reset")
}

func jwtExpiry(tok string) (time.Time, error) {
	parts := strings.Split(tok, ".")
	if len(parts) < 2 {
		return time.Time{}, errors.New("not a JWT")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return time.Time{}, err
	}
	var claims struct {
		Exp int64 `json:"exp"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil || claims.Exp == 0 {
		return time.Time{}, errors.New("no exp claim")
	}
	return time.Unix(claims.Exp, 0), nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
