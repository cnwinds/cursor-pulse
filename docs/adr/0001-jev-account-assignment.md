# ADR 0001: Account assignment scoring (Jev + code)

## Status

Accepted

## Context

Key Loan issuance and Credential Pool intake both pick a Cursor account with a
linear weighted formula (`burn_rate.recommend_lenders`). The weights are
human-fixed. Operators also want:

- a manual base score so some accounts are preferred
- a floor on how often a borrower/session switches accounts (30 minutes)
- scoring that sees Auto (`default` / Composer / grok) vs API Snapshot Headroom
  for the model the borrower will use
- leftover quota digested before reset, without starving the primary member

TypeSafe **Jev** is a System One decision model: state + typed questions in,
calibrated probabilities out. It is a ranking/judgement engine, not a chat
model. TypeSafe’s own composite-scoring pattern says: keep hard constraints and
the combining formula in code; ask Jev only atomic questions.

## Decision

Assignment is a **layered pipeline**. Jev never bypasses safety rails.

```
hard gates (code)
    → Quota Pool features (code)
    → rule score (existing U/S/L/H/F weights)
    → optional Jev composite (loan path only)
    → Manual Rank Score
    → Switch Cooldown
    → pick highest
```

### Hard gates (never Jev)

Unchanged plus pool-aware variants:

- exhausted on the **requested** Quota Pool (or total burn on the loan path
  when the pool is unknown)
- `exhausts_before_reset` on that pool (primary-user protection)
- loan cap, coverage too short
- Snapshot Headroom OR for Credential Pool Intake (unchanged)

### Features (code)

Per candidate, for `auto` / `api` / combined:

- projected surplus on that pool (primary’s continuing burn already subtracted)
- hours to deadline (cycle reset vs subscription end)
- digestion urgency = surplus / time^power
- Manual Rank Score (`proxy_score_adjust`)

The same surplus math is the waste-avoidance signal: idle leftover that will
vanish at reset ranks up; accounts already on track to exhaust are excluded.

### Jev (Key Loan assignment only)

When `jev.enabled` and an API key is set, Pulse sends one TypeSafe
`/v1/systemone` call with all passing candidates as `state` and three atomic
questions per account:

- Noul `waste`: leftover likely wasted at reset if we do not lend
- Noul `safe`: lending is unlikely to squeeze the primary member
- Score `fit`: how good this account is for the requested Quota Pool

Code blends those into `[0,1]` and mixes with the rule score:

`final = (1 - weight_jev) * rule + weight_jev * jev + manual_adjust`

Low confidence or any HTTP failure **fails open** (rule score only). Credential
Pool ranking does **not** call Jev: pool refresh is frequent and the MITM data
plane must stay deterministic.

### Switch Cooldown

Default `min_switch_minutes = 30`. If the borrower already has a usable
assignment younger than the floor, auto-pick keeps it. Exhaustion / hard-gate
failure still switches immediately. Admin manual account pick bypasses the
floor. MITM sticky remains sticky-until-that-Quota-Pool-exhausts; first pick
and rotation-on-exhaust use per-pool scores.

### Quota Pool at request time

- Key Loan recommend/assign accepts `quota_pool` or `model` (`default` / Auto /
  Composer / grok → `auto`; named included models → `api`).
- Credential Pool JSON grows `auto_score` / `api_score`. The Go proxy picks the
  highest score among credentials that still have Snapshot Headroom for the
  request’s Quota Pool.

### Manual Rank Score

`proxy_score_adjust` applies to **both** Credential Pool order and Key Loan
lender ranking. Hard filters still win.

## Consequences

- Operators can set TypeSafe credentials and get Jev-ranked auto-assign without
  rewriting weights; turning Jev off restores the previous formula (plus
  pool-aware surplus and loan-side manual adjust).
- Dual-score pool selection needs a Pulse + Go roll-together so MITM can read
  the new fields (missing scores treat as 0 → list order).
- Jev adds latency only on loan recommend/assign (seconds), never on `/pool`.
