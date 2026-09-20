package main

import (
	"strings"
	"sync"
	"time"
)

type SessionBinding struct {
	ProxyKeyID   string
	PulseKey     string
	Mode         string
	LoanID       string
	CredentialID string
	// StickyCredentialID is the pool credential bound to this CLI session JWT.
	// Run requests prefer this credential for the model's quota pool (auto vs api)
	// until that pool is exhausted, then rotate within the pool order.
	StickyCredentialID string
	// StickySince is when StickyCredentialID was bound. Switch dwell
	// (stickyMinDwell) suppresses quota-driven rotation inside this window so a
	// session does not hop accounts too often. Zero means "no dwell history".
	StickySince time.Time
	// AllowedCredentialIDs scopes pool selection to a ranked candidate set. A
	// loan_alias binding gets this from Pulse (the accounts eligible to lend to
	// that borrower), so the loan key roams across candidate accounts like the
	// shared pool instead of being pinned to one credential. Empty means
	// unscoped: shared-pool keys, or a loan that fell back to its bound key.
	AllowedCredentialIDs []string
	// CursorAPIKey is set for loan_alias so re-exchange uses the bound Cursor key
	// rather than the client-facing pka_ alias.
	CursorAPIKey string
	// WindowLimitReason is set when authorize reports window_limited. Exchange
	// still mints a session JWT so agent login succeeds; business requests then
	// surface a clear resource_exhausted limit error instead of "invalid API key".
	WindowLimitReason string
	BoundAt           time.Time
}

type SessionMap struct {
	mu    sync.RWMutex
	byJWT map[string]SessionBinding
}

func NewSessionMap() *SessionMap {
	return &SessionMap{byJWT: map[string]SessionBinding{}}
}

func (m *SessionMap) Bind(jwt string, b SessionBinding) {
	if b.BoundAt.IsZero() {
		b.BoundAt = time.Now()
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.byJWT[jwt] = b
}

func (m *SessionMap) Lookup(jwt string) (SessionBinding, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	b, ok := m.byJWT[jwt]
	return b, ok
}

func (m *SessionMap) Delete(jwt string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.byJWT, jwt)
}

// allowedSet returns AllowedCredentialIDs as a lookup set, or nil when the
// binding is unscoped. Empty/blank entries collapse to nil so callers can use
// a simple `allowed != nil` test.
func (b SessionBinding) allowedSet() map[string]bool {
	if len(b.AllowedCredentialIDs) == 0 {
		return nil
	}
	out := make(map[string]bool, len(b.AllowedCredentialIDs))
	for _, id := range b.AllowedCredentialIDs {
		if strings.TrimSpace(id) != "" {
			out[id] = true
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
