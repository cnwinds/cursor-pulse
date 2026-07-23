package main

import "sync"

type SessionBinding struct {
	ProxyKeyID   string
	PulseKey     string
	Mode         string
	LoanID       string
	CredentialID string
	// CursorAPIKey is set for loan_alias so re-exchange uses the bound Cursor key
	// rather than the client-facing pka_ alias.
	CursorAPIKey string
}

type SessionMap struct {
	mu    sync.RWMutex
	byJWT map[string]SessionBinding
}

func NewSessionMap() *SessionMap {
	return &SessionMap{byJWT: map[string]SessionBinding{}}
}

func (m *SessionMap) Bind(jwt string, b SessionBinding) {
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
