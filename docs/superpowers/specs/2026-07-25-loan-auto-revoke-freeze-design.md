# Design: Freeze loan auto-revoke deadline

## Problem

`expire_loans_on_reset` compares `account.usage_resets_on` to today. After Cursor sync, that field advances to the *next* cycle end, so loans that should have been reclaimed never match `deadline <= today`.

## Solution (approved: C)

1. Persist `key_loans.expires_on` at issue time (`account_loan_deadline(account)`).
2. Expire when any of:
   - `expires_on <= today` (frozen promise)
   - latest snapshot `cycle_start > loan.created_at.date()` (billing cycle rolled past loan creation)
   - legacy rows with null `expires_on`: fall back to live `account_loan_deadline <= today`
3. Run the same expire pass after a successful Cursor account sync (in addition to daily 03:00 cron).
4. Do **not** backfill `expires_on` from the current (already rolled) `usage_resets_on` — that would freeze the wrong future date. Rely on cycle-rollover for stuck actives.

## API / UI

Payload field remains `loan_expires_on`, sourced from `loan.expires_on` when set, else live account deadline.
