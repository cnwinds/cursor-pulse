# SQLite Chat Concurrency Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox syntax.

**Goal:** Keep SQLite viable for multi-user chat by shortening write locks and recovering from lock failures so users never get silent no-replies.

**Architecture:** Raise busy_timeout; make observability writes best-effort with rollback; requeue jobs on lock errors; commit archive stages (and pre-embed before index writes) so HTTP/LLM work does not hold the SQLite write lock.

**Tech Stack:** Python, SQLAlchemy, SQLite WAL, pytest

## Global Constraints

- No Postgres migration in this change
- Preserve archive stage order and idempotency
- Do not commit secrets or change git config

## File map

| File | Responsibility |
|---|---|
| `assistant_platform/storage/db.py` | `busy_timeout=30000` |
| `assistant_platform/config.py` | `job_processing_timeout_seconds` default 90 |
| `assistant_platform/jobs/db_errors.py` (new) | Detect retryable lock errors |
| `assistant_platform/jobs/worker.py` | Requeue / fail jobs after errors |
| `assistant_platform/conversation/agent_trace.py` | Rollback on persist failure |
| `assistant_platform/conversation/orchestrator.py` | Safe emit_trace + finally rollback |
| `assistant_platform/memory/archive_pipeline.py` | Commit around stages |
| `assistant_platform/memory/archive_indexer.py` | Embed before write burst |
| Tests under `tests/assistant_platform/` | Cover each behavior |

---

### Task 1: Config + busy_timeout

- [x] Test or assert busy_timeout pragma / default timeout value
- [x] Set `PRAGMA busy_timeout=30000`
- [x] Default `job_processing_timeout_seconds=90`

### Task 2: Retryable lock helper + worker requeue

- [x] RED: lock error → job back to `pending`, attempts incremented
- [x] GREEN: implement helper + worker finalize path
- [x] RED: stale processing reclaim still works at 90s semantics in tests

### Task 3: Trace persist must not poison session

- [x] RED: failed persist + rollback allows subsequent DB write
- [x] GREEN: rollback in persist / emit path; orchestrator finally safe

### Task 4: Archive short lock windows

- [x] RED: pipeline commits between stages (spy/commit counter)
- [x] GREEN: commit after PARTIAL and after each stage
- [x] RED/GREEN: index embeds before chunk write burst

### Task 5: Verify

- [x] Run focused pytest suite for touched areas
