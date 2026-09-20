# Cursor Pulse

Self-hosted control plane and optional MITM data plane for Cursor usage, key loans, and quota routing.

## Language

### Identity

**Member**:
A person in the Pulse ledger, with portal role and optional channel identities.
_Avoid_: User, account (for a person)

**MemberIdentity**:
A login or IM addressing key (`channel` + `external_id`) bound to a Member.

### Credentials & loans

**Proxy Key**:
A Pulse-issued alias (`pk_` / `pka_`) that authorizes the MITM proxy to a pool or loan binding.
_Avoid_: API key (ambiguous with Cursor keys)

**Key Loan**:
A temporary binding of an underlying Cursor credential to a borrower via a loan alias.

### Proxy data plane

**Quota Pool**:
The billing bucket a Cursor request consumes — `auto` or `api`. Request-time routing uses model heuristics (Auto vs API are both `INCLUDED`; BYOK PascalCase → unknown). Usage-event stats use Cursor `kind` (`INCLUDED_*` vs `USER_API_KEY`) then model heuristics for Auto vs API. Daily aggregates store `kind_family` so analytics can split Cursor GLM from BYOK. Analytics dimension `external` is BYOK token volume, not a Quota Pool.
_Avoid_: Pool (alone; ambiguous with the credential list), billing pool (implementation phrase); treating BYOK `USER_API_KEY` rows as included API spend; calling BYOK a Quota Pool

**Sticky Credential**:
The pool credential bound to a CLI session JWT for a Quota Pool until that pool is exhausted on that credential, then rotated within pool order.
_Avoid_: Session key, sticky session (overload with HTTP sessions)

**Credential Pool**:
The ordered set of Cursor credentials the MITM may use for non-loan traffic.
_Avoid_: Key pool (when meaning credentials with IDs and per-bucket quota)

**Snapshot Headroom**:
Remaining capacity on a Quota Pool from the latest account snapshot — a percent below 100, or unknown (missing percent counts as usable).
_Avoid_: Remaining cents alone (can disagree with Cursor's percent fields)

**Credential Pool Intake**:
Whether a credential may enter the Credential Pool. Requires Snapshot Headroom on **at least one** Quota Pool (plus burn/coverage filters). Distinct from request-time selection for a **specific** Quota Pool, which may still skip a sticky credential when that bucket is full.
_Avoid_: Exhausted (alone — loan issuance still uses total burn / total_pct)

**Proxy Usage Rollup**:
Account / model / China-calendar-day aggregates over ProxyKeyUsage rows for a Proxy Key or Key Loan.
_Avoid_: Usage analytics (team-wide Cursor sync analytics is a different surface)

**Quota Snapshot Read**:
Newest AccountQuotaSnapshot per account, bulk-loaded for board / lender / Credential Pool Intake.
_Avoid_: Per-account N+1 snapshot queries

**Snapshot Headroom rules**:
Pure OR/AND checks on auto_pct/api_pct used by Credential Pool Intake and mirrored in the Go proxy (`pctQuotaOK` / `snapshotIntakeOK`).
_Avoid_: Embedding these rules only inside burn scoring

**Loan Lifecycle**:
Issuance, state transitions, lender selection, and presentation of a Key Loan — split across dedicated modules behind the key_loans facade.
_Avoid_: Treating key_loans.py as the only place Key Loan logic lives

**CredentialQuotaState**:
Runtime availability of a Credential Pool entry for a Quota Pool — Snapshot Headroom plus per-bucket exhaustion marks; distinct from auth cooldown (`badUntil` / `authCooling`). Go type: `credentialQuotaState` with `availableFor` · `observeExhaustion` · `decayQuotaMarks` · `setAuthCooldown`.
_Avoid_: Collapsing auth cooldown into the same reset as quota marks

**Proxy Authorize / Usage Ledger / Credential Pool Board**:
Caller-facing seams carved from the former `pulse.proxy.service` mega-module (`service.py` remains a facade). Concrete modules: `authorize.authorize_status`, `usage` / `usage_queries` (ledger writes & reads), `usage_rollup` (by_account / by_model / by_day), `pool_board.list_pool_credentials` / `list_pool_ranking_board`, `key_crud`.
_Avoid_: Putting authorize, usage pricing, and pool ranking in one file again; conflating Usage Ledger with Proxy Usage Rollup

**Manual Rank Score**:
Optional per-account delta (`proxy_score_adjust`) added to the computed Assignment Score, used to prefer or demote an account for both Credential Pool order and Key Loan lender selection. Hard filters (Snapshot Headroom / coverage / Switch Cooldown) still apply.
_Avoid_: pin, sticky priority, treating this as a replacement for the computed score or a way to bypass hard filters

**Assignment Score**:
Rank used to auto-pick a lender account (Key Loan) or order the Credential Pool. Rule score (urgency / surplus / load / headroom / freshness on the requested Quota Pool) plus optional Jev blend plus Manual Rank Score.
_Avoid_: treating the linear weights as the only ranking; calling Jev on the MITM hot path

**Quota Pool Surplus**:
Projected leftover cents on Auto or API Snapshot Headroom after the primary member’s current burn continues to deadline. The waste-avoidance signal: idle leftover that will vanish at reset ranks up.
_Avoid_: ranking only on total_pct when the borrower is bound to one Quota Pool; treating remaining cents as a substitute for pool percent

**Switch Cooldown**:
Minimum time (`min_switch_minutes`, default 30) a still-eligible assignment is kept before auto-re-pick may move the borrower to a higher-scoring account. Exhaustion and other hard-gate failures switch immediately. New Key Loan issuance picks the current top Assignment Score (preview matches auto-assign). Admin manual account pick bypasses the floor.
_Avoid_: rotating MITM sticky for score reasons; treating the cooldown as a lock when the current account is unusable; applying cooldown when issuing a new loan so the UI top pick and the issued account diverge

**Jev Rank**:
Optional TypeSafe Jev (System One) composite over atomic waste / primary-safety / pool-fit questions, mixed into Key Loan Assignment Score in code. Fail-open to the rule score. Not used for Credential Pool Intake.
_Avoid_: asking Jev to pick the winner in one question; using chat-completions as a substitute; blocking loan issue when Jev is down
