package main

import (
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
	// CursorAPIKey is set for loan_alias so re-exchange uses the bound Cursor key
	// rather than the client-facing pka_ alias.
	CursorAPIKey string
	BoundAt      time.Time
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
