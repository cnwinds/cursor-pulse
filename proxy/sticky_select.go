package main

import (
	"context"
	"errors"
	"log"
	"time"
)

// StickySelect picks and rotates the sticky credential for a CLI session JWT
// within a known quota pool. Callers resolve the pool kind (auto vs api)
// before Select; mark-on-failure stays with the MITM handler.
type StickySelect struct {
	pool     *Pool
	sessions *SessionMap
}

func NewStickySelect(pool *Pool, sessions *SessionMap) *StickySelect {
	if pool == nil || sessions == nil {
		return nil
	}
	return &StickySelect{pool: pool, sessions: sessions}
}

// Select returns a JWT for the session's sticky credential when that
// credential still has quota for pool; otherwise rotates sticky within the
// pool order and persists via sessions.Bind.
// sessionJWT is the CLI session JWT used as the SessionMap key (not a Proxy Key).
func (s *StickySelect) Select(ctx context.Context, sessionJWT string, binding *SessionBinding, pool quotaPoolKind) (*keyEntry, string, error) {
	now := time.Now()
	if binding.StickyCredentialID != "" {
		stickyID := binding.StickyCredentialID
		entry := s.pool.findEntry(stickyID)
		if entry != nil && !entry.authCooling(now) && entry.availableFor(pool) {
			got, tok, err := s.pool.tokenForCredential(ctx, stickyID)
			if err == nil {
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
			log.Printf("[pool] sticky credential %s lacks %s quota (auto_pct=%s api_pct=%s) — rotating",
				stickyID, pool, formatSnapshotPct(entry.autoPct), formatSnapshotPct(entry.apiPct))
		} else if entry != nil && entry.authCooling(now) {
			log.Printf("[pool] sticky credential %s auth cooling — rotating", stickyID)
		}
		next := s.pool.nextAvailableForQuota(stickyID, pool)
		if next == nil {
			return nil, "", errAllExhausted
		}
		binding.StickyCredentialID = next.credentialID
		if sessionJWT != "" {
			s.sessions.Bind(sessionJWT, *binding)
		}
		log.Printf("[pool] session sticky rotated to credential %s for pool %s", next.credentialID, pool)
		return s.pool.tokenForCredential(ctx, next.credentialID)
	}
	entry, tok, err := s.pool.tokenForQuotaPool(ctx, pool, nil)
	if err != nil {
		return nil, "", err
	}
	binding.StickyCredentialID = entry.credentialID
	if sessionJWT != "" {
		s.sessions.Bind(sessionJWT, *binding)
	}
	return entry, tok, nil
}

// RotateOnExhaustion advances sticky to the next credential with quota for
// pool after the current one was marked exhausted. No-op when none remain.
func (s *StickySelect) RotateOnExhaustion(sessionJWT string, binding *SessionBinding, exhaustedCredID string, pool quotaPoolKind) {
	if s == nil || sessionJWT == "" || binding == nil {
		return
	}
	next := s.pool.nextAvailableForQuota(exhaustedCredID, pool)
	if next == nil {
		log.Printf("[pool] session sticky: no credential available after %s for pool %s", exhaustedCredID, pool)
		return
	}
	if next.credentialID == binding.StickyCredentialID {
		return
	}
	binding.StickyCredentialID = next.credentialID
	s.sessions.Bind(sessionJWT, *binding)
	log.Printf("[pool] session sticky advanced to credential %s for next request (pool %s)", next.credentialID, pool)
}
