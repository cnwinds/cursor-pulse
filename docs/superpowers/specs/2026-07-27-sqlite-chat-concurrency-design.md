# SQLite Chat Concurrency Hardening

## Problem

At 2026-07-27 ~02:39 UTC a user sent「我的用量」and got no reply. Root cause:

1. Concurrent `session.close` archive held SQLite write locks for seconds (embedding / FTS / facts).
2. Interactive worker hit `database is locked` while persisting turn context.
3. Exception was logged but session was not rolled back → `PendingRollbackError`.
4. Job stayed in `processing` (default reclaim timeout 600s) → no reply.

## Goal

Keep SQLite. Support multi-user concurrent chat by:

- Shortening write-lock hold time
- Making lock failures recoverable so users always get a reply or a fast retry

## Non-goals

- Migrating to Postgres
- Splitting archive onto a second physical database (follow-up if still insufficient)

## Design

### 1. Longer wait for brief contention

- Raise SQLite `PRAGMA busy_timeout` from 5000ms to 30000ms.

### 2. Observability writes must not kill the turn

- `persist_agent_trace_event` / turn-context snapshot failures: log, `rollback()`, continue agent run.
- `process_session_job` `finally`: if session is in failed txn state, `rollback()` before `end_turn`.

### 3. Job recovery on lock errors

- On worker failure: always `rollback()`.
- If error is retryable DB lock (`database is locked` / `PendingRollbackError` from lock) and attempts &lt; max (default 5): set job `pending` again.
- Otherwise mark job `failed` (or leave recoverable via stale reclaim).
- Lower `job_processing_timeout_seconds` default from 600 → 90 so crashed workers reclaim faster.

### 4. Shorten archive lock windows

- `run_archive_pipeline`: `commit()` after each successful stage so interactive writers can interleave.
- Keep stage order and idempotency unchanged.

## Success criteria

- Trace persist failure does not leave SQLAlchemy session unusable; final reply can still be produced.
- Lock failure during `session.process` requeues job (status back to `pending`) within attempts budget.
- Archive pipeline commits between stages (test can observe intermediate commits / or mock).
- Existing archive + job claim tests still pass.
