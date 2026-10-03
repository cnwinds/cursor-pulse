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
A Pulse-issued alias (`pk_` / `pka_` / `pkide_`) that authorizes the MITM proxy to a pool or loan binding. `pkide_` is IDE-scoped only (see **IDE Key**); full-power keys remain `pk_` and `pka_`.
_Avoid_: API key (ambiguous with Cursor keys)

**IDE Key** (`pkide_`):
A scoped derivative of a quota `ProxyKey` or `proxy_alias` Key Loan, stored on the parent row (`ide_key_hash` / `ide_encrypted_key`). One active `pkide_` per parent; authorization reuses the parent's row checks with `scope=ide`. Used only for Cursor IDE attribution via `http.proxy` userinfo on the shared main port — not for CLI exchange, OpenAI gateway, or Pulse admin APIs. Usage, model routing, and limits match the parent key.
_Avoid_: Treating `pkide_` as interchangeable with `pk_`/`pka_`; putting full proxy keys in IDE `http.proxy` userinfo

**Key Loan**:
A temporary binding of an underlying Cursor credential to a borrower via a loan alias.

**Coding Plan Account** (GLM / MiniMax / Kimi):
An `AiAccount` whose vendor uses **quota-only** sync (Coding Plan monitor APIs — no usage-event history). Portal ledger and quota board use **separate tabs and card layouts** from Cursor. **Not** eligible for Credential Pool, Key Loan, or the Cursor MITM proxy (`proxy_enabled` stays off). Optional **OpenAI-compatible gateway** via `cp_proxy_enabled` + `pkcp_` on Go proxy `/openai/v1` (ADR 0003) — separate HTTP path from Cursor CONNECT MITM.
_Avoid_: Treating CP percent windows as Cursor Quota Pools (auto/api); ingesting fake usage events

### Proxy data plane

**Quota Pool**:
The billing bucket a Cursor request consumes — `auto` or `api`. Request-time routing uses model heuristics (Auto vs API are both `INCLUDED`); BYOK third-party models and requests without a model count as `auto`. There is no third pool at request time. Usage-event stats use Cursor `kind` (`INCLUDED_*` vs `USER_API_KEY`) then model heuristics for Auto vs API. Daily aggregates store `kind_family` so analytics can split Cursor GLM from BYOK. Analytics dimension `external` is BYOK token volume, not a Quota Pool.
_Avoid_: Pool (alone; ambiguous with the credential list), billing pool (implementation phrase); treating BYOK `USER_API_KEY` rows as included API spend; calling BYOK a Quota Pool

**Sticky Credential**:
The pool credential bound to a CLI session JWT for one Quota Pool. Each session has two independent slots (auto, api); each is filled and rotated only from its own pool's order — an api request never reuses the auto slot's credential just because it has api headroom.
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
Pure OR/AND checks on auto_pct/api_pct used by Credential Pool Intake and mirrored in the Go proxy (`pctQuotaOK`).
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
Snapshot Headroom read for one Quota Pool (`auto` vs `api`) instead of the included total, used when a target model is known. Resolved by `quota_pool.quota_pool_for_model` on the web side, mirroring Go `quotaPoolForModel` for non-empty models. `unknown` (loan issuance without a target model only) falls back to total and requires both buckets.
_Avoid_: Per-pool cents as an exact figure (Cursor exposes per-bucket percents only; cents are a monotone share of `limit_cents`)

**Owner Reserve**:
Per-account percentage (`proxy_reserve_pct`, default from `loan_selection.owner_reserve_pct`) that must stay unused for the account's primary owner. Projected owner burn at the pool deadline above `100 - reserve` hard-excludes the account as a lender (`owner_reserve`).
_Avoid_: Confusing with Snapshot Headroom (headroom is live state; reserve is a policy floor)

**Switch Dwell**:
Minimum idle gap (`loan_selection.min_switch_minutes`, Go `PROXY_STICKY_MIN_DWELL`) between two proxy requests before quota pressure may rotate the sticky credential. While requests arrive within that window (dense chat), the proxy keeps the same account so upstream prompt cache stays warm. Go uses `SessionBinding.StickyLastActive`; Coding Plan OpenAI stickiness uses `CpOpenAiStickyBinding.updated_at`. `StickySince` / `sticky_since` record when the credential was bound, not the dwell clock. Within the window the account is only demoted by `recency_penalty` on the scoring side and held by the proxy, not hard-excluded, so a pool never becomes unusable.
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

**Membership Plan**:
A team-level template defining default **Spend Rules** (rolling 5h / 7d / 30d × `auto` / `api` / `total`) and **Credit Mode** (`unlimited` or `prepaid`), plus optional one-time `opening_credit_cents` on first assignment. Plans are `active` or `archived`; archived plans cannot be assigned to new members but existing memberships keep referencing them until changed.
_Avoid_: Treating a plan edit as retroactive to past charges; deleting plans that still have active members

**Membership**:
At most one `active` row per Member binding them to a Membership Plan (or a custom rules/mode override). No billing cycle and no expiry — cancel sets `cancelled` but leaves the **Credit Account** balance intact. Effective spend policy = overrides when set, else plan fields; plan edits apply immediately to all members on that plan.
_Avoid_: Confusing membership status with credit balance; expecting caps to reset when a plan changes

**Credit Mode** (`unlimited` / `prepaid`):
`unlimited` — window **Spend Rules** only; no `charge` rows. `prepaid` — each counted usage row debits the wallet at record time; `evaluate_spend` blocks both pools when balance ≤ 0. Mode comes from `credit_mode_override` or the plan.
_Avoid_: Expecting unlimited members to see charge lines; using prepaid without admin grants

**Credit Account**:
Per-member wallet (`credit_accounts.balance_cents`), independent of membership lifecycle. Balance may go negative briefly because usage posts after the Run (no pre-auth). Top-ups and corrections go through append-only **Credit Transactions**.
_Avoid_: Deriving balance from live `ProxyKeyUsage` sums; assuming balance resets on plan change

**Credit Transaction** (`grant` / `charge` / `refund` / `adjust`):
Append-only ledger lines with signed `amount_cents` and stored `balance_after_cents`. `charge` links `usage_id` (unique) and freezes cost at record time; `refund` points to one `charge` once; `adjust` requires a note. Grants include opening credit (`note=membership:opening_credit`, once per member lifetime).
_Avoid_: Editing or deleting rows; expecting `reprice_proxy_usages` to rewrite charges

**Credit Statement**:
Paginated `credit_transactions` joined to usage for charge detail (model, pool, `client`, key/loan label, tokens). Summary `by_day` / `by_model` is aggregated from **transactions** in the selected interval so totals reconcile with the wallet, not from `usage_rollup` (repricing can change historical usage cost).
_Avoid_: Using Proxy Usage Rollup cents as the member bill total

**Spend Rule**:
Member-scoped rolling cap: period (`5h` / `week` / `month`) + pool (`auto` / `api` / `total`) + `limit_cents`. Multiple rules are OR for the request's pool. Usage sums `ProxyKeyUsage` for the member (`pk_` + `pka_` + `pkide_` via `member_id`), bucketed by `usage_cap_pool`. Orphan `proxy_alias` loans without a borrower still use loan-scoped legacy rules. Enforced on `AgentService/Run` via **Spend Check**, not at authorize.
_Avoid_: Per-key `window_5h` / `window_7d` on authorize; `quota_pool_for_model` for caps; counting `cr*` passthrough or `pkcp_`

**Spend Check**:
Internal `POST /api/internal/v1/proxy/spend-check` → `evaluate_spend` (BYOK → membership required → prepaid exhausted → rule exceeded). Go calls it before upstream `RoundTrip` on non-`loan_passthrough` bindings; 429 with Python `message`, 503 fail-closed on check failure.
_Avoid_: Blocking exchange/login on cap; caching a failed check as a permanent deny

**Loan Usage Cap** (superseded):
Former per-loan rolling Auto/API ceilings (ADR-0004). **Superseded by Spend Rule on Membership** (ADR-0006) for members with a borrower; orphan proxy-alias loans may still carry loan-scoped rules until migrated. Management `usage_caps` / `PATCH .../usage-cap` and Cursor `pk_` window fields are deprecated inputs.
_Avoid_: Configuring caps on loans or proxy keys instead of the member membership UI

**Jev Decision**:
The TypeSafe System One decision model reached through OpenRouter's Decisions endpoint (`/api/alpha/decisions`), not chat completions. Re-ranks the surviving Top-N lenders and answers a per-candidate "safe for the owner" question. Advisory only: hard filters are authoritative and the deterministic score is the fallback.
_Avoid_: Treating Jev as an LLM text model; putting it in the request path (it runs on pool refresh and loan issuance — the Auto-Assigned Loan candidate allowlist is ordered by the deterministic score alone)

**Per-key IDE Port** (removed):
Former dedicated proxy listen port per Proxy Key (`GET /ide-port`, default base 9100, `ide_ports.json`). **Superseded by IDE Key on the shared main port** (ADR 0005); kept for one transition release (v0.7.0) and removed afterwards — `/ide-port` now returns 404.
_Avoid_: Reintroducing per-key ports instead of `pkide_` userinfo on the main port

**Login Identity Lock**:
Optional pin of a Proxy Key to the first parseable IDE login JWT `sub` seen on it (`PROXY_IDE_LOCK_SUB`); rejects `alg=none` / missing sub / control characters in sub; a different identity is rejected with 403 (`ide_sub_mismatch`). Applies on the shared main port; not persisted across restarts. Does **not** verify WorkOS JWT signatures (TOFU trust boundary).
_Avoid_: treating unsigned claim parsing as cryptographic identity proof.
