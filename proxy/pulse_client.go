package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
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
}

type PoolCredential struct {
	CredentialID string `json:"credential_id"`
	APIKey       string `json:"api_key"`
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

	authTTL time.Duration
	authMu  sync.Mutex
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
		baseURL:         stringsTrimRightSlash(baseURL),
		token:           token,
		client:          &http.Client{Timeout: 15 * time.Second},
		authTTL:         authTTL,
		authCache:       map[string]struct {
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
	c.authMu.Lock()
	if e, ok := c.authCache[pulseKey]; ok && time.Now().Before(e.expiry) {
		res := e.res
		c.authMu.Unlock()
		return res, nil
	}
	c.authMu.Unlock()

	body, _ := json.Marshal(map[string]string{"pulse_key": pulseKey})
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
	// loan_alias carries cursor_api_key and must re-check loan status — never cache.
	if res.Mode == "loan_alias" {
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
