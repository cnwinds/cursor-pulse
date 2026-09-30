package main

import (
	"context"
	"errors"
	"log"
	"strings"
	"time"
)

// SeatAdvisor asks Pulse which credential this session should use for pool.
// current is the pool's sticky credential ("" when the slot is empty); release
// is true when current is being left. Pulse picks from pool's order only.
// advised false or a non-nil error means fail open. advised true with an empty
// assignment means fail closed.
type SeatAdvisor func(binding *SessionBinding, current string, release bool, pool quotaPoolKind) (assigned string, blocked []string, advised bool, err error)

// StickySelect picks and rotates a CLI session's sticky credentials. Each
// Quota Pool has its own slot, filled from that pool's order; callers resolve
// the pool (auto vs api) before Select. Mark-on-failure stays with the MITM
// handler.
type StickySelect struct {
	pool     *Pool
	sessions *SessionMap
	// minDwell suppresses quota-driven rotation while the slot was active less
	// than this long ago. Auth failure and pool-wide exhaustion still rotate: a
	// session must never get stuck on an unusable account.
	minDwell time.Duration
	advisor  SeatAdvisor
}

func NewStickySelect(pool *Pool, sessions *SessionMap) *StickySelect {
	return NewStickySelectWithDwell(pool, sessions, 0)
}

func NewStickySelectWithDwell(pool *Pool, sessions *SessionMap, minDwell time.Duration) *StickySelect {
	if pool == nil || sessions == nil {
		return nil
	}
	if minDwell < 0 {
		minDwell = 0
	}
	return &StickySelect{pool: pool, sessions: sessions, minDwell: minDwell}
}

func (s *StickySelect) SetAdvisor(fn SeatAdvisor) {
	if s == nil {
		return
	}
	s.advisor = fn
}

// dwellActive reports whether pool's slot is still "active" for Switch dwell:
// a request is still being relayed on it (a long agent stream has not ended),
// or the idle gap since it last served is inside minDwell. A slot that has no
// LastActive yet falls back to Since.
func (s *StickySelect) dwellActive(binding *SessionBinding, pool quotaPoolKind, now time.Time) bool {
	if s.minDwell <= 0 {
		return false
	}
	if binding.inFlight[pool] > 0 {
		return true
	}
	slot := binding.sticky(pool)
	ref := slot.LastActive
	if ref.IsZero() {
		ref = slot.Since
	}
	if ref.IsZero() {
		return false
	}
	return now.Sub(ref) < s.minDwell
}

// persist stores only pool's slot (plus the latest seat advice when
// withBlocked) so a concurrent request on the other slot is not overwritten.
func (s *StickySelect) persist(sessionJWT string, binding *SessionBinding, pool quotaPoolKind, withBlocked bool) {
	if sessionJWT == "" {
		return
	}
	slot := *binding.sticky(pool)
	blocked := binding.BlockedCredentialIDs
	if !s.sessions.Update(sessionJWT, func(stored *SessionBinding) {
		*stored.sticky(pool) = slot
		if withBlocked {
			stored.BlockedCredentialIDs = blocked
		}
	}) {
		s.sessions.Bind(sessionJWT, *binding)
	}
}

// refreshSlots picks up slots other requests on this session stored since
// binding was copied, so Select neither refills a slot twice nor reports a
// stale held credential.
func (s *StickySelect) refreshSlots(sessionJWT string, binding *SessionBinding) {
	if sessionJWT == "" {
		return
	}
	if stored, ok := s.sessions.Lookup(sessionJWT); ok {
		binding.AutoSticky = stored.AutoSticky
		binding.APISticky = stored.APISticky
		binding.inFlight = stored.inFlight
	}
}

// Track marks a request on pool's slot as in flight until the returned func
// runs, which also refreshes LastActive: a long agent stream counts as active
// for its whole run, and Switch dwell is measured from when it ended.
func (s *StickySelect) Track(sessionJWT string, pool quotaPoolKind) (done func()) {
	if s == nil || sessionJWT == "" {
		return func() {}
	}
	if !s.sessions.Update(sessionJWT, func(b *SessionBinding) { b.inFlight[pool]++ }) {
		return func() {}
	}
	return func() {
		s.sessions.Update(sessionJWT, func(b *SessionBinding) {
			if b.inFlight[pool] > 0 {
				b.inFlight[pool]--
			}
			if slot := b.sticky(pool); slot.CredentialID != "" {
				slot.LastActive = time.Now()
			}
		})
	}
}

func (s *StickySelect) touchActive(sessionJWT string, binding *SessionBinding, pool quotaPoolKind, now time.Time) {
	slot := binding.sticky(pool)
	if slot.Since.IsZero() {
		slot.Since = now
	}
	slot.LastActive = now
	s.persist(sessionJWT, binding, pool, false)
}

// bindSticky fills pool's slot, refreshing Since only when the credential
// actually changed (a re-bind of the same credential keeps the clock).
func (s *StickySelect) bindSticky(sessionJWT string, binding *SessionBinding, pool quotaPoolKind, credentialID string, now time.Time) {
	slot := binding.sticky(pool)
	if slot.CredentialID != credentialID || slot.Since.IsZero() {
		slot.Since = now
	}
	slot.CredentialID = credentialID
	slot.LastActive = now
	s.persist(sessionJWT, binding, pool, true)
}

// Select returns a JWT for pool's sticky credential while it still has quota
// for pool; otherwise picks a new one from pool's order and persists that slot.
// An empty slot is filled from pool's order as well.
// sessionJWT is the CLI session JWT used as the SessionMap key (not a Proxy Key).
func (s *StickySelect) Select(ctx context.Context, sessionJWT string, binding *SessionBinding, pool quotaPoolKind) (*keyEntry, string, error) {
	now := time.Now()
	s.refreshSlots(sessionJWT, binding)
	// nil for shared-pool keys; a ranked candidate set for loan_alias bindings.
	allowed := binding.allowedSet()
	slot := binding.sticky(pool)
	stickyID := slot.CredentialID
	if stickyID == "" {
		return s.fill(ctx, sessionJWT, binding, pool, allowed, now)
	}
	if !binding.slotActive(pool, now) {
		id, err := s.reseat(sessionJWT, binding, pool, stickyID, allowed, now)
		if err != nil {
			return nil, "", err
		}
		stickyID = id
	}

	entry := s.pool.findEntry(stickyID)
	// A credential dropped from the candidate set must not keep serving.
	if entry != nil && allowed != nil && !allowed[stickyID] {
		log.Printf("[pool] %s sticky credential %s left the candidate set — rotating", pool, stickyID)
		entry = nil
	}
	switch {
	case entry != nil && !entry.authCooling(now) && entry.availableFor(pool):
		got, tok, err := s.pool.tokenForCredential(ctx, stickyID)
		if err == nil {
			s.touchActive(sessionJWT, binding, pool, now)
			return got, tok, nil
		}
		// Transient exchange errors must not rotate sticky (align with tokenSkipping).
		if got != nil && !errors.Is(err, errAllExhausted) && !isPermanentExchangeErr(err) {
			return got, "", err
		}
		if got != nil && isPermanentExchangeErr(err) {
			// Exchange/auth bad-key marking belongs here with sticky
			// rotation; MITM failKind quota marks stay in Server.mark.
			log.Printf("[pool] %s sticky credential %s exchange failed: %v - marking bad", pool, stickyID, err)
			s.pool.markBad(got)
		}
	case entry != nil && !entry.authCooling(now):
		if s.dwellActive(binding, pool, now) {
			// Switch dwell: keep the account for now rather than hop on the
			// first sign of bucket exhaustion. The request may still fail on
			// quota, which is a clearer signal than silent account churn.
			log.Printf("[pool] %s sticky credential %s lacks quota (auto_pct=%s api_pct=%s) — holding within dwell",
				pool, stickyID, formatSnapshotPct(entry.autoPct), formatSnapshotPct(entry.apiPct))
			if got, tok, err := s.pool.tokenForCredential(ctx, stickyID); err == nil {
				s.touchActive(sessionJWT, binding, pool, now)
				return got, tok, nil
			}
		}
		log.Printf("[pool] %s sticky credential %s lacks quota (auto_pct=%s api_pct=%s) — rotating",
			pool, stickyID, formatSnapshotPct(entry.autoPct), formatSnapshotPct(entry.apiPct))
	case entry != nil:
		log.Printf("[pool] %s sticky credential %s auth cooling — rotating", pool, stickyID)
	}

	if id, handled, err := s.chooseAssigned(binding, stickyID, true, pool, allowed); handled {
		if err != nil {
			return nil, "", err
		}
		s.bindSticky(sessionJWT, binding, pool, id, now)
		log.Printf("[pool] %s sticky rotated to credential %s (seat)", pool, id)
		return s.pool.tokenForCredential(ctx, id)
	}
	next := s.pool.nextAvailableForQuotaWithin(stickyID, pool, allowed, binding.blockedSet())
	if next == nil {
		return nil, "", errAllExhausted
	}
	s.bindSticky(sessionJWT, binding, pool, next.credentialID, now)
	log.Printf("[pool] %s sticky rotated to credential %s", pool, next.credentialID)
	return s.pool.tokenForCredential(ctx, next.credentialID)
}

// reseat re-reports an idle slot's credential before it serves again: its
// seat was left to expire, so the account may have filled up meanwhile. Pulse
// keeps the credential when there is room (cache stays warm) or assigns
// another from pool's order. Quota checks stay with the caller so Switch
// dwell still applies. Advice unavailable keeps the credential (fail open).
func (s *StickySelect) reseat(sessionJWT string, binding *SessionBinding, pool quotaPoolKind, stickyID string, allowed map[string]bool, now time.Time) (string, error) {
	if s.advisor == nil || strings.TrimSpace(binding.PulseKey) == "" {
		return stickyID, nil
	}
	assigned, blocked, advised, err := s.advisor(binding, stickyID, false, pool)
	if err != nil || !advised {
		log.Printf("[pool] seat advice unavailable: %v", err)
		return stickyID, nil
	}
	binding.BlockedCredentialIDs = blocked
	assigned = strings.TrimSpace(assigned)
	if assigned == "" {
		return "", errAllExhausted
	}
	if assigned == stickyID {
		return stickyID, nil
	}
	if (allowed != nil && !allowed[assigned]) || s.pool.findEntry(assigned) == nil {
		// Pulse already seated us on an account we cannot use here: release
		// that seat and ask again, like any other unusable assignment.
		id, handled, err := s.chooseAssigned(binding, assigned, true, pool, allowed)
		if !handled {
			return stickyID, nil
		}
		if err != nil {
			return "", err
		}
		assigned = id
	}
	if assigned == stickyID {
		return stickyID, nil
	}
	s.bindSticky(sessionJWT, binding, pool, assigned, now)
	log.Printf("[pool] %s sticky re-seated from %s to credential %s after idle", pool, stickyID, assigned)
	return assigned, nil
}

// fill picks the first credential for an empty slot from pool's order: Pulse
// seat advice when available, else the local order.
func (s *StickySelect) fill(ctx context.Context, sessionJWT string, binding *SessionBinding, pool quotaPoolKind, allowed map[string]bool, now time.Time) (*keyEntry, string, error) {
	if id, handled, err := s.chooseAssigned(binding, "", false, pool, allowed); handled {
		if err != nil {
			return nil, "", err
		}
		s.bindSticky(sessionJWT, binding, pool, id, now)
		log.Printf("[pool] %s sticky bound to credential %s (seat)", pool, id)
		return s.pool.tokenForCredential(ctx, id)
	}
	entry, tok, err := s.pool.tokenForQuotaPoolWithin(ctx, pool, binding.blockedSet(), allowed)
	if err != nil {
		return nil, "", err
	}
	s.bindSticky(sessionJWT, binding, pool, entry.credentialID, now)
	return entry, tok, nil
}

// RotateOnExhaustion replaces pool's sticky credential after it was marked
// exhausted. No-op when the slot has already moved or none remain. This path
// is driven by an observed quota failure, so Switch dwell does not apply —
// the account is known-unusable.
func (s *StickySelect) RotateOnExhaustion(sessionJWT string, binding *SessionBinding, exhaustedCredID string, pool quotaPoolKind) {
	if s == nil || sessionJWT == "" || binding == nil {
		return
	}
	s.refreshSlots(sessionJWT, binding)
	if binding.sticky(pool).CredentialID != exhaustedCredID {
		return
	}
	if id, handled, err := s.chooseAssigned(binding, exhaustedCredID, true, pool, binding.allowedSet()); handled {
		if err != nil || id == "" || id == exhaustedCredID {
			log.Printf("[pool] %s sticky: no credential available after %s", pool, exhaustedCredID)
			return
		}
		s.bindSticky(sessionJWT, binding, pool, id, time.Now())
		log.Printf("[pool] %s sticky advanced to credential %s for next request (seat)", pool, id)
		return
	}
	next := s.pool.nextAvailableForQuotaWithin(exhaustedCredID, pool, binding.allowedSet(), binding.blockedSet())
	if next == nil {
		log.Printf("[pool] %s sticky: no credential available after %s", pool, exhaustedCredID)
		return
	}
	s.bindSticky(sessionJWT, binding, pool, next.credentialID, time.Now())
	log.Printf("[pool] %s sticky advanced to credential %s for next request", pool, next.credentialID)
}

// chooseAssigned asks Pulse for a credential from pool's order that is in the
// pool, in the allowlist, and still has quota for pool. handled means the
// caller must not fall through to a local pick: either use id, or treat err as
// exhaustion.
func (s *StickySelect) chooseAssigned(binding *SessionBinding, current string, release bool, pool quotaPoolKind, allowed map[string]bool) (string, bool, error) {
	if s == nil || s.advisor == nil || binding == nil || strings.TrimSpace(binding.PulseKey) == "" {
		return "", false, nil
	}
	released := strings.TrimSpace(current)
	askRelease := release
	seen := map[string]bool{}
	limit := s.pool.size()
	if limit < 1 {
		limit = 1
	}
	for i := 0; i < limit; i++ {
		assigned, blocked, advised, err := s.advisor(binding, released, askRelease, pool)
		if advised {
			binding.BlockedCredentialIDs = blocked
		}
		if err != nil || !advised {
			log.Printf("[pool] seat advice unavailable: %v", err)
			return "", false, nil
		}
		assigned = strings.TrimSpace(assigned)
		if assigned == "" || seen[assigned] {
			return "", true, errAllExhausted
		}
		seen[assigned] = true
		if allowed != nil && !allowed[assigned] {
			released = assigned
			askRelease = true
			continue
		}
		got := s.pool.findEntry(assigned)
		if got == nil || !got.availableFor(pool) {
			released = assigned
			askRelease = true
			continue
		}
		return assigned, true, nil
	}
	return "", true, errAllExhausted
}
