package main

import (
	"context"
	"errors"
	"log"
	"strings"
	"time"
)

// SeatAdvisor asks Pulse which credential to move to. release is true when
// current is being left (quota exhaustion). advised false or a non-nil error
// means fail open. advised true with an empty assignment means fail closed.
type SeatAdvisor func(binding *SessionBinding, current string, release bool, quotaSkip map[string]bool) (assigned string, blocked []string, advised bool, err error)

// StickySelect picks and rotates the sticky credential for a CLI session JWT
// within a known quota pool. Callers resolve the pool kind (auto vs api)
// before Select; mark-on-failure stays with the MITM handler.
type StickySelect struct {
	pool     *Pool
	sessions *SessionMap
	// minDwell suppresses quota-driven rotation while the sticky credential was
	// bound less than this long ago. Auth failure and pool-wide exhaustion still
	// rotate: a session must never get stuck on an unusable account.
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

// dwellActive reports whether binding was refreshed inside the dwell window.
// A zero StickySince is legacy state (pre-dwell) and never blocks rotation.
func (s *StickySelect) dwellActive(binding *SessionBinding, now time.Time) bool {
	if s.minDwell <= 0 || binding.StickySince.IsZero() {
		return false
	}
	return now.Sub(binding.StickySince) < s.minDwell
}

// bindSticky records a sticky binding, refreshing StickySince only when the
// credential actually changed (a re-bind of the same credential keeps the clock).
func (s *StickySelect) bindSticky(sessionJWT string, binding *SessionBinding, credentialID string, now time.Time) {
	if binding.StickyCredentialID != credentialID || binding.StickySince.IsZero() {
		binding.StickySince = now
	}
	binding.StickyCredentialID = credentialID
	if sessionJWT != "" {
		s.sessions.Bind(sessionJWT, *binding)
	}
}

// Select returns a JWT for the session's sticky credential when that
// credential still has quota for pool; otherwise rotates sticky within the
// pool order and persists via sessions.Bind.
// sessionJWT is the CLI session JWT used as the SessionMap key (not a Proxy Key).
func (s *StickySelect) Select(ctx context.Context, sessionJWT string, binding *SessionBinding, pool quotaPoolKind) (*keyEntry, string, error) {
	now := time.Now()
	// nil for shared-pool keys; a ranked candidate set for loan_alias bindings.
	allowed := binding.allowedSet()
	if binding.StickyCredentialID != "" {
		stickyID := binding.StickyCredentialID
		entry := s.pool.findEntry(stickyID)
		// A credential dropped from the candidate set must not keep serving.
		if entry != nil && allowed != nil && !allowed[stickyID] {
			log.Printf("[pool] sticky credential %s left the candidate set — rotating", stickyID)
			entry = nil
		}
		if entry != nil && !entry.authCooling(now) && entry.availableFor(pool) {
			got, tok, err := s.pool.tokenForCredential(ctx, stickyID)
			if err == nil {
				// Dwell measures time since the last switch, so a plain reuse
				// must not refresh StickySince (that would freeze the window and
				// block rotation forever). Only backfill legacy zero state.
				if binding.StickySince.IsZero() {
					binding.StickySince = now
					if sessionJWT != "" {
						s.sessions.Bind(sessionJWT, *binding)
					}
				}
				return got, tok, nil
			}
			// Transient exchange errors must not rotate sticky (align with tokenSkipping).
			if got != nil && !errors.Is(err, errAllExhausted) && !isPermanentExchangeErr(err) {
				return got, "", err
			}
			if got != nil && isPermanentExchangeErr(err) {
				// Exchange/auth bad-key marking belongs here with sticky
				// rotation; MITM failKind quota marks stay in Server.mark.
				log.Printf("[pool] sticky credential %s exchange failed: %v - marking bad", stickyID, err)
				s.pool.markBad(got)
			}
		} else if entry != nil && !entry.authCooling(now) && !entry.availableFor(pool) {
			if s.dwellActive(binding, now) {
				// Switch dwell: keep the account for now rather than hop on the
				// first sign of bucket exhaustion. The request may still fail on
				// quota, which is a clearer signal than silent account churn.
				log.Printf("[pool] sticky credential %s lacks %s quota (auto_pct=%s api_pct=%s) — holding within dwell",
					stickyID, pool, formatSnapshotPct(entry.autoPct), formatSnapshotPct(entry.apiPct))
				if got, tok, err := s.pool.tokenForCredential(ctx, stickyID); err == nil {
					return got, tok, nil
				}
			}
			log.Printf("[pool] sticky credential %s lacks %s quota (auto_pct=%s api_pct=%s) — rotating",
				stickyID, pool, formatSnapshotPct(entry.autoPct), formatSnapshotPct(entry.apiPct))
		} else if entry != nil && entry.authCooling(now) {
			log.Printf("[pool] sticky credential %s auth cooling — rotating", stickyID)
		}
		if id, handled, err := s.chooseAssigned(binding, stickyID, true, pool, allowed); handled {
			if err != nil {
				return nil, "", err
			}
			s.bindSticky(sessionJWT, binding, id, now)
			log.Printf("[pool] session sticky rotated to credential %s for pool %s (seat)", id, pool)
			return s.pool.tokenForCredential(ctx, id)
		}
		next := s.pool.nextAvailableForQuotaWithin(stickyID, pool, allowed, binding.blockedSet())
		if next == nil {
			return nil, "", errAllExhausted
		}
		s.bindSticky(sessionJWT, binding, next.credentialID, now)
		log.Printf("[pool] session sticky rotated to credential %s for pool %s", next.credentialID, pool)
		return s.pool.tokenForCredential(ctx, next.credentialID)
	}
	entry, tok, err := s.pool.tokenForQuotaPoolWithin(ctx, pool, binding.blockedSet(), allowed)
	if err != nil {
		return nil, "", err
	}
	s.bindSticky(sessionJWT, binding, entry.credentialID, now)
	return entry, tok, nil
}

// RotateOnExhaustion advances sticky to the next credential with quota for
// pool after the current one was marked exhausted. No-op when none remain.
// This path is driven by an observed quota failure, so Switch dwell does not
// apply — the account is known-unusable.
func (s *StickySelect) RotateOnExhaustion(sessionJWT string, binding *SessionBinding, exhaustedCredID string, pool quotaPoolKind) {
	if s == nil || sessionJWT == "" || binding == nil {
		return
	}
	if id, handled, err := s.chooseAssigned(binding, exhaustedCredID, true, pool, binding.allowedSet()); handled {
		if err != nil || id == "" || id == binding.StickyCredentialID {
			log.Printf("[pool] session sticky: no credential available after %s for pool %s", exhaustedCredID, pool)
			return
		}
		s.bindSticky(sessionJWT, binding, id, time.Now())
		log.Printf("[pool] session sticky advanced to credential %s for next request (pool %s, seat)", id, pool)
		return
	}
	next := s.pool.nextAvailableForQuotaWithin(exhaustedCredID, pool, binding.allowedSet(), binding.blockedSet())
	if next == nil {
		log.Printf("[pool] session sticky: no credential available after %s for pool %s", exhaustedCredID, pool)
		return
	}
	if next.credentialID == binding.StickyCredentialID {
		return
	}
	s.bindSticky(sessionJWT, binding, next.credentialID, time.Now())
	log.Printf("[pool] session sticky advanced to credential %s for next request (pool %s)", next.credentialID, pool)
}

// chooseAssigned asks Pulse for a credential that is in the pool, in the
// allowlist, and still has quota. handled means the caller must not fall
// through to a local pick: either use id, or treat err as exhaustion.
func (s *StickySelect) chooseAssigned(binding *SessionBinding, current string, release bool, pool quotaPoolKind, allowed map[string]bool) (string, bool, error) {
	if s == nil || s.advisor == nil || binding == nil || strings.TrimSpace(binding.PulseKey) == "" {
		return "", false, nil
	}
	released := strings.TrimSpace(current)
	askRelease := release
	seen := map[string]bool{}
	quotaSkip := map[string]bool{}
	limit := s.pool.size()
	if limit < 1 {
		limit = 1
	}
	for i := 0; i < limit; i++ {
		assigned, blocked, advised, err := s.advisor(binding, released, askRelease, quotaSkip)
		if advised {
			binding.BlockedCredentialIDs = blocked
		}
		if err != nil || !advised {
			log.Printf("[pool] seat advice unavailable: %v", err)
			return "", false, nil
		}
		assigned = strings.TrimSpace(assigned)
		if assigned == "" {
			if id := s.quotaFallbackPick(current, pool, allowed, binding.blockedSet(), quotaSkip); id != "" {
				log.Printf("[pool] seat advice empty for quota pool %s — local quota pick %s", pool, id)
				return id, true, nil
			}
			return "", true, errAllExhausted
		}
		if seen[assigned] {
			if id := s.quotaFallbackPick(current, pool, allowed, binding.blockedSet(), quotaSkip); id != "" {
				log.Printf("[pool] seat advice loop on %s — local quota pick %s", assigned, id)
				return id, true, nil
			}
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
			quotaSkip[assigned] = true
			released = assigned
			askRelease = true
			continue
		}
		return assigned, true, nil
	}
	if id := s.quotaFallbackPick(current, pool, allowed, binding.blockedSet(), quotaSkip); id != "" {
		log.Printf("[pool] seat advice exhausted quota retries — local quota pick %s", id)
		return id, true, nil
	}
	return "", true, errAllExhausted
}

// quotaFallbackPick runs when Pulse seat advice cannot yield a credential that
// still has Quota Pool headroom. It does not override concurrency fail-closed
// (empty advice with no quotaSkip is handled above).
func (s *StickySelect) quotaFallbackPick(current string, pool quotaPoolKind, allowed, blocked, quotaSkip map[string]bool) string {
	if s == nil || s.pool == nil || len(quotaSkip) == 0 {
		return ""
	}
	skip := map[string]bool{}
	for id := range quotaSkip {
		skip[id] = true
	}
	for id := range blocked {
		skip[id] = true
	}
	next := s.pool.nextAvailableForQuotaWithin(strings.TrimSpace(current), pool, allowed, skip)
	if next == nil {
		return ""
	}
	return next.credentialID
}
