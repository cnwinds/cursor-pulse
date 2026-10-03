package main

import (
	"strings"
	"sync"
	"time"
)

type SessionBinding struct {
	ProxyKeyID   string
	PulseKey     string
	Client       string // "cli" (exchange) or "ide" (tunnel bind)
	Mode         string
	LoanID       string
	CredentialID string
	// AutoSticky / APISticky are the pool credentials bound to this CLI session
	// JWT, one per Quota Pool. Each was picked from that pool's order and only
	// serves that pool's requests: an api request never reuses the auto account
	// just because it happens to have api headroom.
	AutoSticky stickySlot
	APISticky  stickySlot
	// inFlight counts requests still being relayed per Quota Pool slot (index
	// is the quotaPoolKind). A long agent Run stream keeps its slot active even
	// though no new request arrives. Only SessionMap.Update may change it;
	// never write a whole binding copy back over it.
	inFlight [2]int
	// AllowedCredentialIDs scopes pool selection to a ranked candidate set. A
	// loan_alias binding gets this from Pulse (the accounts eligible to lend to
	// that borrower), so the loan key roams across candidate accounts like the
	// shared pool instead of being pinned to one credential. Empty means
	// unscoped: shared-pool keys, or a loan that fell back to its bound key.
	AllowedCredentialIDs []string
	// BlockedCredentialIDs are accounts already at the concurrent-user cap for
	// this holder. Local selection skips them when Pulse seat advice fails open.
	BlockedCredentialIDs []string
	// CursorAPIKey is set for loan_alias so re-exchange uses the bound Cursor key
	// rather than the client-facing pka_ alias.
	CursorAPIKey string
	BoundAt      time.Time
	// ExpiresAt is the exp of the proxy-minted client token; Prune drops the
	// binding after sessionPruneGrace. Zero never expires.
	ExpiresAt time.Time
}

type stickySlot struct {
	CredentialID string
	// Since is when CredentialID was bound. Refreshed only on credential change.
	Since time.Time
	// LastActive is when this slot last served: a successful Select, or the end
	// of a relayed request. Switch dwell (stickyMinDwell) suppresses
	// quota-driven rotation while the gap since this timestamp is inside the
	// window, so dense chat keeps cache.
	LastActive time.Time
}

// slotIdleAfter is how long a slot with no request in flight stays active
// after it last served. It matches Pulse's default seat TTL
// (concurrent_ttl_seconds = 180): an idle slot stops holding its seat.
const slotIdleAfter = 3 * time.Minute

func (b *SessionBinding) sticky(pool quotaPoolKind) *stickySlot {
	if pool == quotaPoolAPI {
		return &b.APISticky
	}
	return &b.AutoSticky
}

// slotActive reports whether pool's slot is in use: a request is still being
// relayed on it, or it served within slotIdleAfter.
func (b *SessionBinding) slotActive(pool quotaPoolKind, now time.Time) bool {
	slot := b.sticky(pool)
	if slot.CredentialID == "" {
		return false
	}
	if b.inFlight[pool] > 0 {
		return true
	}
	ref := slot.LastActive
	if ref.IsZero() {
		ref = slot.Since
	}
	return !ref.IsZero() && now.Sub(ref) < slotIdleAfter
}

// heldOutside lists the other pool's sticky credential while that slot is
// active, so a seat report for pool keeps that seat alive and never drops it
// on release. An idle slot is left out: its seat is allowed to expire.
func (b *SessionBinding) heldOutside(pool quotaPoolKind) []string {
	other := quotaPoolAuto
	if pool == quotaPoolAuto {
		other = quotaPoolAPI
	}
	if !b.slotActive(other, time.Now()) {
		return nil
	}
	return []string{b.sticky(other).CredentialID}
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

// Update applies fn to the stored binding under the lock. Requests on one
// session run concurrently with their own binding copies; writing back a whole
// copy would clobber a slot another request just filled. Returns false when
// jwt is not bound.
func (m *SessionMap) Update(jwt string, fn func(*SessionBinding)) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	b, ok := m.byJWT[jwt]
	if !ok {
		return false
	}
	fn(&b)
	m.byJWT[jwt] = b
	return true
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

// Prune drops bindings whose ExpiresAt is more than sessionPruneGrace before
// now and returns how many were removed.
func (m *SessionMap) Prune(now time.Time) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	removed := 0
	for jwt, b := range m.byJWT {
		if !b.ExpiresAt.IsZero() && now.After(b.ExpiresAt.Add(sessionPruneGrace)) {
			delete(m.byJWT, jwt)
			removed++
		}
	}
	return removed
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

func (b SessionBinding) blockedSet() map[string]bool {
	if len(b.BlockedCredentialIDs) == 0 {
		return nil
	}
	out := make(map[string]bool, len(b.BlockedCredentialIDs))
	for _, id := range b.BlockedCredentialIDs {
		if strings.TrimSpace(id) != "" {
			out[id] = true
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
