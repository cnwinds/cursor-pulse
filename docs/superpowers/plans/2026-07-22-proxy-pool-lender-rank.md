# Proxy Pool Lender Rank + FastRepo Auth Skip

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox syntax.

**Goal:** Rank `/proxy/pool` with `recommend_lenders`; do not markBad on FastRepo/Repository 401.

**Architecture:** Pulse sorts credentials before returning the pool; Go resets `cur` on replace and skips rotation on non-fatal auth paths.

**Tech Stack:** Python/FastAPI, Go MITM proxy, existing `burn_rate.recommend_lenders`.

## Global Constraints

- Hard-filter empty pool → empty credentials list (no fallback).
- Protocol unchanged: `{credential_id, api_key}` only.
- Path skip: substring `FastRepo` or `RepositoryService`.

---

### Task 1: Rank pool credentials (Python)

**Files:**
- Create/modify: `pulse/proxy/service.py` (or thin helper) — `list_pool_credentials`
- Modify: `pulse/web/internal_proxy_api.py`
- Test: `tests/test_web_internal_proxy.py`

- [x] Write failing tests: order by recommend score; exclude exhausted; exclude no-snapshot
- [x] Implement ranking using `recommend_lenders` + same loan_selection config
- [x] Run pytest for internal proxy tests

### Task 2: FastRepo skip + cur reset (Go)

**Files:**
- Modify: `proxy/connect.go` or `proxy/mitm.go` — `isNonFatalAuthPath`
- Modify: `proxy/pool.go` — `ReplaceFromPulse` sets `cur=0`
- Test: `proxy/connect_test.go`, `proxy/pool_*.go`

- [x] Write failing tests
- [x] Implement
- [x] `go test ./...` in `proxy/`
