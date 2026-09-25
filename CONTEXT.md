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

**Coding Plan Account** (GLM / MiniMax / Kimi):
An `AiAccount` whose vendor uses **quota-only** sync (Coding Plan monitor APIs — no usage-event history). Portal ledger and quota board use **separate tabs and card layouts** from Cursor. **Not** eligible for Credential Pool, Key Loan, or the Cursor MITM proxy (`proxy_enabled` stays off). Optional **OpenAI-compatible gateway** via `cp_proxy_enabled` + `pkcp_` keys (`/openai/v1`, ADR 0003) — separate from Go MITM.
_Avoid_: Treating CP percent windows as Cursor Quota Pools (auto/api); ingesting fake usage events

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
Optional per-account delta (`proxy_score_adjust`) added to the computed ranking score, used to fine-tune Credential Pool order **and** Key Loan lender selection. Hard filters (Snapshot Headroom / coverage / owner reserve) still apply.
_Avoid_: pin, sticky priority, treating this as a replacement for the computed score

**Pool-scoped Headroom**:
Snapshot Headroom read for one Quota Pool (`auto` vs `api`) instead of the included total, used when a target model is known. Resolved by `quota_pool.quota_pool_for_model` on the web side, mirroring Go `quotaPoolForModel`; `unknown` falls back to total and requires both buckets.
_Avoid_: Per-pool cents as an exact figure (Cursor exposes per-bucket percents only; cents are a monotone share of `limit_cents`)

**Owner Reserve**:
Per-account percentage (`proxy_reserve_pct`, default from `loan_selection.owner_reserve_pct`) that must stay unused for the account's primary owner. Projected owner burn at the pool deadline above `100 - reserve` hard-excludes the account as a lender (`owner_reserve`).
_Avoid_: Confusing with Snapshot Headroom (headroom is live state; reserve is a policy floor)

**Switch Dwell**:
Minimum time (`loan_selection.min_switch_minutes`, Go `PROXY_STICKY_MIN_DWELL`) a credential stays bound before quota pressure may rotate it. `SessionBinding.StickySince` is the clock; within the window the account is only demoted by `recency_penalty` on the scoring side and held by the proxy, not hard-excluded, so a pool never becomes unusable.
_Avoid_: Treating it as a hard lock (exhaustion and auth failure still rotate)

**Concurrent Seat**:
Live occupancy of one account by distinct proxy users. The Go proxy reports its current credential when it authorizes (exchange, session reauth) and when sticky rotation leaves that credential. Web counts one seat per member per account (else per loan or proxy key); two sessions of the same member on one account are one seat, and the same member on two accounts holds both. A seat expires after `loan_selection.concurrent_ttl_seconds` (default 180). A new pool joiner is refused once `loan_selection.max_concurrent_users` (default 3, 0 = unlimited) other holders are already on that account. A holder already seated is not evicted. A designated loan keeps its credential and still occupies a seat. An empty candidate list is not a cap rejection. The count is proxy-mediated only and process-local.
_Avoid_: Confusing it with `max_active_loans_per_account` (open loans, not live users) or with the primary owner using Cursor outside the proxy

**Designated Loan** (`lender_mode=manual`):
A Key Loan pinned to one lending account: issuance creates a dedicated Cursor key (`key_role=loan`) there, and the proxy serves that loan through it. `reassign_loan_source` re-pins it. Authorization returns no candidate list.
_Avoid_: Confusing the loan key with the account's primary key

**Auto-Assigned Loan** (`lender_mode=auto`):
Self-service Key Loan whose key roams across candidate accounts during use. Authorization returns a ranked allowlist of candidate **primary** credentials (`pool_board.loan_candidate_credentials`); Go selects within it exactly like the Credential Pool — per-session sticky, Switch Dwell, per-Quota-Pool availability. Switching needs no new Cursor key.
_Avoid_: Reassigning at the DB level to change accounts (that was retired — it needed a new remote key per switch and could only move on a timer)

**Pool-Routed Loan** (`routing_mode=pool`):
Admin auto-assign. The pka_ has no source account and no Cursor key. Authorization returns `mode=loan_pool`; the proxy selects from the Credential Pool (accounts toggled into the pool, ordered by the ranking board) the same way a legacy `pk_` key does. Usage is attributed to `loan_id`. New members should get this instead of a `pk_`.
_Avoid_: Treating it as a Designated Loan, or as the self-service allowlist loan

**Auto Lender Selection**:
The ranking behind both loan modes: hard filters, then the deterministic score, then the optional Jev decision (`tool_center.auto_lender`). Used at issuance to pick the starting account and, for auto-assigned loans, to build the proxy's candidate allowlist.
_Avoid_: Treating it as the thing that switches accounts at request time (the proxy does that)

**Jev Decision**:
The TypeSafe System One decision model reached through OpenRouter's Decisions endpoint (`/api/alpha/decisions`), not chat completions. Re-ranks the surviving Top-N lenders and answers a per-candidate "safe for the owner" question. Advisory only: hard filters are authoritative and the deterministic score is the fallback.
_Avoid_: Treating Jev as an LLM text model; putting it in the request path (it runs on pool refresh and loan issuance — the Auto-Assigned Loan candidate allowlist is ordered by the deterministic score alone)
