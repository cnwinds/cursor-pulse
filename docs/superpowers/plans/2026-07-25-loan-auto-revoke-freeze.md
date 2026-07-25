# Loan Auto-Revoke Freeze Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Freeze reclaim date at loan issue and expire on that date or billing-cycle rollover (cron + post-sync).

**Architecture:** Add nullable `KeyLoan.expires_on`. Set at issue. `expire_loans_on_reset` uses frozen date + cycle_start vs created_at + legacy fallback. Hook after `CursorSyncService.sync_account` success.

**Tech Stack:** SQLAlchemy models, migrate.py ADD COLUMN, pytest.

## File map

- `pulse/storage/models.py` — `expires_on` column
- `pulse/storage/migrate.py` — ADD COLUMN
- `pulse/tool_center/key_loans.py` — create/issue/payload/expire helpers
- `pulse/ingestion/sync.py` — post-sync expire
- `tests/test_lender_selection.py` — new + updated cases

## Tasks

1. Model + migrate `expires_on DATE`
2. Set on `create_loan_record` / `issue_loan_key`; payload prefers frozen
3. Rewrite `expire_loans_on_reset` with three triggers
4. Call expire after successful sync commit
5. Tests for freeze, cycle rollover, payload; run pytest subset
