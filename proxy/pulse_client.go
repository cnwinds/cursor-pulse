package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

type AuthResult struct {
	Status       string  `json:"status"`
	ProxyKeyID   string  `json:"proxy_key_id"`
	Mode         string  `json:"mode"`
	LoanID       string  `json:"loan_id"`
	CredentialID string  `json:"credential_id"`
	CursorAPIKey string  `json:"cursor_api_key,omitempty"`
	Reason       *string `json:"reason"`
	// CredentialIDs is the ranked candidate allowlist for a loan_alias in auto
	// mode: the loan roams across these accounts like the shared pool. Empty
	// means pinned to CredentialID / CursorAPIKey (designated loan).
	CredentialIDs []string `json:"credential_ids,omitempty"`
	// Seat advice from Pulse. SeatAdvised false means the web did not judge
	// concurrency (no candidates, or the advisor failed): fail open to local
	// select. An empty AssignedCredentialID with SeatAdvised true means there
	// is nowhere to put this holder without passing the cap, including when
	// the credential just released cannot be chosen again.
	AssignedCredentialID string   `json:"assigned_credential_id,omitempty"`
	BlockedCredentialIDs []string `json:"blocked_credential_ids,omitempty"`
	MaxConcurrentUsers   int      `json:"max_concurrent_users,omitempty"`
	SeatAdvised          bool     `json:"seat_advised,omitempty"`
}

type PoolCredential struct {
	CredentialID string   `json:"credential_id"`
	APIKey       string   `json:"api_key"`
	AutoPct      *float64 `json:"auto_pct"`
	ApiPct       *float64 `json:"api_pct"`
}

type TokenCounts struct {
	Input      int64 `json:"input"`
	Output     int64 `json:"output"`
	CacheRead  int64 `json:"cache_read"`
	CacheWrite int64 `json:"cache_write"`
	Reasoning  int64 `json:"reasoning"`
}

type UsageItem struct {
	ProxyKeyID   string      `json:"proxy_key_id,omitempty"`
	LoanID       string      `json:"loan_id,omitempty"`
	CredentialID string      `json:"credential_id,omitempty"`
	Model        string      `json:"model,omitempty"`
	Tokens       TokenCounts `json:"tokens"`
	TS           string      `json:"ts,omitempty"`
	RequestID    string      `json:"request_id,omitempty"`
}

type EventItem struct {
	EventType    string `json:"event_type"`
	ProxyKeyID   string `json:"proxy_key_id,omitempty"`
	LoanID       string `json:"loan_id,omitempty"`
	CredentialID string `json:"credential_id,omitempty"`
	Detail       string `json:"detail,omitempty"`
}

// LoanUsageCapResult is the internal loan usage cap check (HTTP 200; limited is in body).
type LoanUsageCapResult struct {
	Status          string `json:"status"`
	Reason          string `json:"reason"`
	Pool            string `json:"pool"`
	Period          string `json:"period"`
	UsedCents       int    `json:"used_cents"`
	LimitCents      *int   `json:"limit_cents"`
	ResetsAt        string `json:"resets_at"`
	OtherPool       string `json:"other_pool"`
	OtherPoolOpen   bool   `json:"other_pool_open"`
	Message         string `json:"message"`
}

type PulseClient struct {
	baseURL string
	token   string
	client  *http.Client

	authTTL   time.Duration
	authMu    sync.Mutex
	authCache map[string]struct {
		res    AuthResult
		expiry time.Time
	}

	usageBatchMax   int
	usageFlushEvery time.Duration
	usageMaxRetries int
	usageBufMax     int // hard cap; overflow drops newest after a failed flush requeue
	usageMu         sync.Mutex
	usageBuf        []UsageItem
	usageRetryAfter time.Time // backoff after a failed flush (ignored when force)
	stopped         bool
	startMu         sync.Mutex
	started         bool
	stopCh          chan struct{}
	wg              sync.WaitGroup
}

const defaultUsageBufMax = 2000

func NewPulseClient(baseURL, token string, authTTL time.Duration) *PulseClient {
	if authTTL <= 0 {
		authTTL = 60 * time.Second
	}
	return &PulseClient{
		baseURL: stringsTrimRightSlash(baseURL),
		token:   token,
		client: &http.Client{
			Timeout:   15 * time.Second,
			Transport: bootHeaderTransport{boot: newBootID(), next: http.DefaultTransport},
		},
		authTTL: authTTL,
		authCache: map[string]struct {
			res    AuthResult
			expiry time.Time
		}{},
		usageBatchMax:   50,
		usageFlushEvery: 5 * time.Second,
		usageMaxRetries: 3,
		usageBufMax:     defaultUsageBufMax,
		stopCh:          make(chan struct{}),
	}
}

// bootHeaderTransport stamps every Pulse call with this process's boot id. Pulse uses
// it (plus the periodic pool poll) to tell when a proxy died with gateway calls in flight.
type bootHeaderTransport struct {
	boot string
	next http.RoundTripper
}

func (t bootHeaderTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req = req.Clone(req.Context())
	req.Header.Set("X-Proxy-Boot", t.boot)
	return t.next.RoundTrip(req)
}

func newBootID() string {
	var b [12]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("t%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

func stringsTrimRightSlash(s string) string {
	for len(s) > 0 && s[len(s)-1] == '/' {
		s = s[:len(s)-1]
	}
	return s
}

func (c *PulseClient) Start() {
	c.startMu.Lock()
	if c.started {
		c.startMu.Unlock()
		return
	}
	c.usageMu.Lock()
	if c.stopped {
		c.usageMu.Unlock()
		c.startMu.Unlock()
		return
	}
	c.usageMu.Unlock()
	c.started = true
	c.startMu.Unlock()

	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		t := time.NewTicker(c.usageFlushEvery)
		defer t.Stop()
		for {
			select {
			case <-c.stopCh:
				c.flushUsage(true)
				return
			case <-t.C:
				c.flushUsage(false)
			}
		}
	}()
}

func (c *PulseClient) Stop() {
	c.usageMu.Lock()
	if c.stopped {
		c.usageMu.Unlock()
		return
	}
	c.stopped = true
	c.usageMu.Unlock()

	select {
	case <-c.stopCh:
	default:
		close(c.stopCh)
	}
	c.wg.Wait()
	c.flushUsage(true)
}

func (c *PulseClient) Authorize(pulseKey string) (AuthResult, error) {
	return c.authorize(pulseKey, seatReport{})
}

// AuthorizeFresh drops any cached authorize result for the key, then calls
// Authorize. Used by IDE /ide-port and TOFU bind so revoke/suspend is not
// delayed by the auth TTL cache.
func (c *PulseClient) AuthorizeFresh(pulseKey string) (AuthResult, error) {
	c.authMu.Lock()
	delete(c.authCache, pulseKey)
	c.authMu.Unlock()
	return c.Authorize(pulseKey)
}

// AuthorizeSeat is Authorize plus seat advice for one Quota Pool slot of a
// session. current is that slot's credential ("" when empty); release means
// it is being left. held lists the session's other slot credentials so their
// seats stay alive. Pulse only assigns from pool's order. Seat calls bypass
// the auth cache: they must refresh the occupancy seat.
func (c *PulseClient) AuthorizeSeat(pulseKey, currentCredentialID string, releaseCurrent bool, pool quotaPoolKind, held []string) (AuthResult, error) {
	return c.authorize(pulseKey, seatReport{
		seat:    true,
		current: strings.TrimSpace(currentCredentialID),
		release: releaseCurrent,
		pool:    pool,
		held:    held,
	})
}

type seatReport struct {
	seat    bool
	current string
	release bool
	pool    quotaPoolKind
	held    []string
}

func (c *PulseClient) authorize(pulseKey string, seat seatReport) (AuthResult, error) {
	report := seat.seat
	if !report {
		c.authMu.Lock()
		if e, ok := c.authCache[pulseKey]; ok && time.Now().Before(e.expiry) {
			res := e.res
			c.authMu.Unlock()
			return res, nil
		}
		c.authMu.Unlock()
	}

	payload := map[string]any{"pulse_key": pulseKey}
	if seat.seat {
		payload["quota_pool"] = seat.pool.String()
		if seat.current != "" {
			payload["current_credential_id"] = seat.current
		}
		if seat.release {
			payload["release_current"] = true
		}
		// held may equal current: when both slots share a credential, releasing
		// one slot must not drop the seat the other slot still uses.
		var held []string
		for _, id := range seat.held {
			if id = strings.TrimSpace(id); id != "" {
				held = append(held, id)
			}
		}
		if len(held) > 0 {
			payload["held_credential_ids"] = held
		}
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/authorize", bytes.NewReader(body))
	if err != nil {
		return AuthResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return AuthResult{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return AuthResult{}, fmt.Errorf("authorize HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var res AuthResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return AuthResult{}, err
	}
	// loan_alias carries cursor_api_key; loan_pool must see revoke immediately.
	// Seat reports must not be cached or the occupancy heartbeat dies.
	// Rejects (invalid/suspended/…) are also never cached so a recreated key
	// is not stuck behind a stale deny, and a revoke is not masked by a prior ok
	// once the TTL expires — callers that need immediate revoke use AuthorizeFresh.
	if report || res.Mode == "loan_alias" || res.Mode == "loan_pool" {
		return res, nil
	}
	if res.Status != "ok" && res.Status != "window_limited" {
		return res, nil
	}
	cached := res
	cached.CursorAPIKey = "" // never keep Cursor secrets in the TTL cache
	c.authMu.Lock()
	c.authCache[pulseKey] = struct {
		res    AuthResult
		expiry time.Time
	}{res: cached, expiry: time.Now().Add(c.authTTL)}
	c.authMu.Unlock()
	return res, nil
}

func (c *PulseClient) CheckLoanUsageCap(loanID, model string) (LoanUsageCapResult, error) {
	payload := map[string]string{
		"loan_id": loanID,
		"model":   model,
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/loan-usage-cap", bytes.NewReader(body))
	if err != nil {
		return LoanUsageCapResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return LoanUsageCapResult{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return LoanUsageCapResult{}, fmt.Errorf("loan usage cap HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var res LoanUsageCapResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return LoanUsageCapResult{}, err
	}
	return res, nil
}

// OpenAIResolveResult is returned by the Coding Plan resolve internal API.
type OpenAIResolveResult struct {
	Status           string `json:"status"`
	ProxyKeyID       string `json:"proxy_key_id"`
	CodingPlanVendor string `json:"coding_plan_vendor"`
	CredentialID     string `json:"credential_id"`
	APIKey           string `json:"api_key"`
	UpstreamChatURL  string `json:"upstream_chat_url"`
	Reason           string `json:"reason"`
}

func (c *PulseClient) ResolveOpenAI(
	pulseKey string,
	excludeCredentialIDs []string,
	currentCredentialID string,
	releaseCurrent bool,
) (OpenAIResolveResult, error) {
	payload := map[string]any{
		"pulse_key":              pulseKey,
		"exclude_credential_ids": excludeCredentialIDs,
	}
	if currentCredentialID != "" {
		payload["current_credential_id"] = currentCredentialID
	}
	if releaseCurrent {
		payload["release_current"] = true
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/openai-proxy/resolve", bytes.NewReader(body))
	if err != nil {
		return OpenAIResolveResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return OpenAIResolveResult{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return OpenAIResolveResult{}, fmt.Errorf("openai resolve HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var res OpenAIResolveResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return OpenAIResolveResult{}, err
	}
	return res, nil
}

var endOpenAIBackoff = []time.Duration{time.Second, 5 * time.Second, 30 * time.Second}

// EndOpenAI tells Pulse a gateway call finished so the seat is kept for the dwell window.
// Retries a few times: a lost end leaves the seat in flight until this process stops polling.
func (c *PulseClient) EndOpenAI(proxyKeyID, credentialID string) {
	body, _ := json.Marshal(map[string]any{
		"proxy_key_id":  proxyKeyID,
		"credential_id": credentialID,
	})
	var lastErr error
	for attempt := 0; ; attempt++ {
		lastErr = c.postEndOpenAI(body)
		if lastErr == nil {
			return
		}
		if attempt >= len(endOpenAIBackoff) {
			break
		}
		time.Sleep(endOpenAIBackoff[attempt])
	}
	log.Printf("[openai] end call: %v", lastErr)
}

func (c *PulseClient) postEndOpenAI(body []byte) error {
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/openai-proxy/end", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	resp, err := c.client.Do(req.WithContext(ctx))
	if err != nil {
		return err
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return nil
}

func (c *PulseClient) RecordOpenAIUsage(proxyKeyID, credentialID, model string, usage map[string]any) error {
	payload := map[string]any{
		"proxy_key_id":  proxyKeyID,
		"credential_id": credentialID,
		"model":         model,
		"usage":         usage,
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/openai-proxy/usage", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req = req.WithContext(ctx)
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("openai usage HTTP %d", resp.StatusCode)
	}
	return nil
}

func (c *PulseClient) FetchPool() (PulsePoolSnapshot, error) {
	req, err := http.NewRequest(http.MethodGet, c.baseURL+"/api/internal/v1/proxy/pool", nil)
	if err != nil {
		return PulsePoolSnapshot{}, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return PulsePoolSnapshot{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != http.StatusOK {
		return PulsePoolSnapshot{}, fmt.Errorf("pool HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var out struct {
		Credentials       []PoolCredential `json:"credentials"`
		CredentialsByPool struct {
			Auto []PoolCredential `json:"auto"`
			API  []PoolCredential `json:"api"`
		} `json:"credentials_by_pool"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return PulsePoolSnapshot{}, err
	}
	return PulsePoolSnapshot{
		Default: out.Credentials,
		Auto:    out.CredentialsByPool.Auto,
		API:     out.CredentialsByPool.API,
	}, nil
}

func (c *PulseClient) EnqueueUsage(item UsageItem) {
	if item.TS == "" {
		item.TS = time.Now().UTC().Format(time.RFC3339)
	}
	c.usageMu.Lock()
	if c.stopped {
		c.usageMu.Unlock()
		return
	}
	c.usageBuf = append(c.usageBuf, item)
	flushNow := len(c.usageBuf) >= c.usageBatchMax
	c.usageMu.Unlock()
	if flushNow {
		c.flushUsage(false)
	}
}

func (c *PulseClient) ReportEvent(ev EventItem) {
	payload, _ := json.Marshal(map[string]any{"events": []EventItem{ev}})
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/events", bytes.NewReader(payload))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req = req.WithContext(ctx)
	resp, err := c.client.Do(req)
	if err != nil {
		log.Printf("[pulse] report event: %v", err)
		return
	}
	resp.Body.Close()
}

func (c *PulseClient) flushUsage(force bool) {
	c.usageMu.Lock()
	if len(c.usageBuf) == 0 {
		c.usageMu.Unlock()
		return
	}
	if !force && !c.usageRetryAfter.IsZero() && time.Now().Before(c.usageRetryAfter) {
		c.usageMu.Unlock()
		return
	}
	batch := append([]UsageItem(nil), c.usageBuf...)
	c.usageBuf = c.usageBuf[:0]
	c.usageMu.Unlock()

	payload, _ := json.Marshal(map[string]any{"items": batch})
	var lastErr error
	for attempt := 0; attempt < c.usageMaxRetries; attempt++ {
		req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/usage", bytes.NewReader(payload))
		if err != nil {
			lastErr = err
			break
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+c.token)
		resp, err := c.client.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(attempt+1) * 200 * time.Millisecond)
			continue
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			c.usageMu.Lock()
			c.usageRetryAfter = time.Time{}
			c.usageMu.Unlock()
			return
		}
		lastErr = fmt.Errorf("usage HTTP %d", resp.StatusCode)
		time.Sleep(time.Duration(attempt+1) * 200 * time.Millisecond)
	}
	log.Printf("[pulse] usage flush failed (%d items): %v (force=%v); requeue", len(batch), lastErr, force)
	c.requeueUsage(batch, !force)
}

// requeueUsage puts a failed batch back at the front of the buffer (oldest first).
// When withBackoff is true, subsequent non-force flushes wait briefly so an outage
// does not tight-loop on every EnqueueUsage that hits usageBatchMax.
func (c *PulseClient) requeueUsage(batch []UsageItem, withBackoff bool) {
	if len(batch) == 0 {
		return
	}
	c.usageMu.Lock()
	defer c.usageMu.Unlock()
	c.usageBuf = append(batch, c.usageBuf...)
	max := c.usageBufMax
	if max <= 0 {
		max = defaultUsageBufMax
	}
	if len(c.usageBuf) > max {
		overflow := len(c.usageBuf) - max
		c.usageBuf = c.usageBuf[:max]
		log.Printf("[pulse] usage buffer full; dropped %d newest item(s) (cap=%d)", overflow, max)
	}
	if withBackoff {
		// ~1s per retry attempt, floor 2s — next ticker / enqueue can try again.
		backoff := time.Duration(c.usageMaxRetries) * time.Second
		if backoff < 2*time.Second {
			backoff = 2 * time.Second
		}
		c.usageRetryAfter = time.Now().Add(backoff)
	}
}
