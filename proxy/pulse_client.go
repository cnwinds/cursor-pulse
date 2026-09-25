package main

import (
	"bytes"
	"context"
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
	usageMu         sync.Mutex
	usageBuf        []UsageItem
	stopped         bool
	startMu         sync.Mutex
	started         bool
	stopCh          chan struct{}
	wg              sync.WaitGroup
}

func NewPulseClient(baseURL, token string, authTTL time.Duration) *PulseClient {
	if authTTL <= 0 {
		authTTL = 60 * time.Second
	}
	return &PulseClient{
		baseURL: stringsTrimRightSlash(baseURL),
		token:   token,
		client:  &http.Client{Timeout: 15 * time.Second},
		authTTL: authTTL,
		authCache: map[string]struct {
			res    AuthResult
			expiry time.Time
		}{},
		usageBatchMax:   50,
		usageFlushEvery: 5 * time.Second,
		usageMaxRetries: 3,
		stopCh:          make(chan struct{}),
	}
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
	return c.authorize(pulseKey, "", false)
}

// AuthorizeReport is Authorize plus the credential this session is on.
// A non-empty current credential or release=true bypasses the auth cache:
// session reauth and rotation must refresh the occupancy seat.
func (c *PulseClient) AuthorizeReport(pulseKey, currentCredentialID string, releaseCurrent bool) (AuthResult, error) {
	return c.authorize(pulseKey, strings.TrimSpace(currentCredentialID), releaseCurrent)
}

func (c *PulseClient) authorize(pulseKey, currentCredentialID string, releaseCurrent bool) (AuthResult, error) {
	report := currentCredentialID != "" || releaseCurrent
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
	if currentCredentialID != "" {
		payload["current_credential_id"] = currentCredentialID
	}
	if releaseCurrent {
		payload["release_current"] = true
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
	// Neither is cached.
	if report || res.Mode == "loan_alias" || res.Mode == "loan_pool" {
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

func (c *PulseClient) ResolveOpenAI(pulseKey string, excludeCredentialIDs []string) (OpenAIResolveResult, error) {
	payload := map[string]any{
		"pulse_key":                pulseKey,
		"exclude_credential_ids": excludeCredentialIDs,
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

func (c *PulseClient) FetchPool() ([]PoolCredential, error) {
	req, err := http.NewRequest(http.MethodGet, c.baseURL+"/api/internal/v1/proxy/pool", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("pool HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var out struct {
		Credentials []PoolCredential `json:"credentials"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out.Credentials, nil
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
			return
		}
		lastErr = fmt.Errorf("usage HTTP %d", resp.StatusCode)
		time.Sleep(time.Duration(attempt+1) * 200 * time.Millisecond)
	}
	log.Printf("[pulse] usage flush dropped %d items after retries: %v (force=%v)", len(batch), lastErr, force)
}
