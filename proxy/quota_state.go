package main

import "time"

// credentialQuotaState is CredentialQuotaState: Snapshot Headroom, per-bucket
// runtime exhaustion marks, and auth cooldown. JWT cache stays on keyEntry.
type credentialQuotaState struct {
	autoQuotaExhausted bool
	apiQuotaExhausted  bool
	autoPct            *float64 // Pulse snapshot: planUsage.autoPercentUsed
	apiPct             *float64 // Pulse snapshot: planUsage.apiPercentUsed
	badUntil           time.Time
}

func (q *credentialQuotaState) quotaFullyExhausted() bool {
	return q.autoQuotaExhausted && q.apiQuotaExhausted
}

func (q *credentialQuotaState) runtimeQuotaOK(pool quotaPoolKind) bool {
	switch pool {
	case quotaPoolAuto:
		return !q.autoQuotaExhausted
	case quotaPoolAPI:
		return !q.apiQuotaExhausted
	default:
		return !q.quotaFullyExhausted()
	}
}

func (q *credentialQuotaState) snapshotQuotaOK(pool quotaPoolKind) bool {
	switch pool {
	case quotaPoolAuto:
		return pctQuotaOK(q.autoPct)
	case quotaPoolAPI:
		return pctQuotaOK(q.apiPct)
	default:
		return pctQuotaOK(q.autoPct) && pctQuotaOK(q.apiPct)
	}
}

// availableFor reports Snapshot Headroom plus runtime marks for pool.
// Auth cooldown is authCooling — not folded in here.
func (q *credentialQuotaState) availableFor(pool quotaPoolKind) bool {
	if pool == quotaPoolUnknown {
		return !q.quotaFullyExhausted() && q.snapshotQuotaOK(pool)
	}
	return q.runtimeQuotaOK(pool) && q.snapshotQuotaOK(pool)
}

// hasQuotaForPool is the historical name for availableFor (tests / call sites).
func (q *credentialQuotaState) hasQuotaForPool(pool quotaPoolKind) bool {
	return q.availableFor(pool)
}

func (q *credentialQuotaState) authCooling(now time.Time) bool {
	return !q.badUntil.IsZero() && now.Before(q.badUntil)
}

// unavailable is full-account skip: both buckets exhausted or auth cooldown.
func (q *credentialQuotaState) unavailable() bool {
	return q.quotaFullyExhausted() || q.authCooling(time.Now())
}

func (q *credentialQuotaState) setFullyQuotaExhausted() {
	q.autoQuotaExhausted = true
	q.apiQuotaExhausted = true
}

// observeExhaustion marks pool exhausted. Returns whether state changed.
func (q *credentialQuotaState) observeExhaustion(pool quotaPoolKind) bool {
	switch pool {
	case quotaPoolAuto:
		if q.autoQuotaExhausted {
			return false
		}
		q.autoQuotaExhausted = true
		return true
	case quotaPoolAPI:
		if q.apiQuotaExhausted {
			return false
		}
		q.apiQuotaExhausted = true
		return true
	default:
		if q.quotaFullyExhausted() {
			return false
		}
		q.setFullyQuotaExhausted()
		return true
	}
}

// decayQuotaMarks clears runtime Quota Pool exhaustion only.
func (q *credentialQuotaState) decayQuotaMarks() {
	q.autoQuotaExhausted = false
	q.apiQuotaExhausted = false
}

func (q *credentialQuotaState) setAuthCooldown(until time.Time) {
	q.badUntil = until
}

func (q *credentialQuotaState) clearAuthCooldown() {
	q.badUntil = time.Time{}
}
