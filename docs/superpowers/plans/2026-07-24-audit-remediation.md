# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Critical/Important security, correctness, and documentation gaps found in the 2026-07-24 project audit — without large package refactors.

**Architecture:** Keep Pulse (control plane) / Assistant (orchestration) / Go Proxy (data plane) boundaries. Prefer small fail-closed checks, signed actor claims, session TTL + re-authorize, and doc/env alignment. No monorepo split.

**Tech Stack:** Python 3.11+ (FastAPI, httpx, pytest), Go 1.22 (proxy), Markdown docs, Docker Compose.

## Global Constraints

- Work on branch `fix/audit-remediation-2026-07-24` only; do not push unless asked.
- Prefer minimal diffs; no drive-by refactors or unrelated renames.
- TDD: failing test first, then implement, then re-run focused tests.
- Commit after each task with a concise conventional message (`fix:` / `docs:` / `sec:`).
- Do not weaken existing security tests; extend them.
- Chinese user-facing error strings may stay Chinese; new code comments in English OK.
- Proxy default must remain usable for local/trusted LAN; harden with flags/env, tighten defaults where safe (`127.0.0.1` listen optional via env, CONNECT allowlist for cursor hosts by default).

## File map (touched)

| Area | Files |
|------|--------|
| Tokens | `pulse/security_tokens.py`, `assistant_platform/config.py`, `assistant_platform/app.py`, `tests/test_security_tokens.py`, new assistant tests |
| Actor HMAC | `pulse/web/assistant_actor.py` (new), `pulse/web/assistant_*_api.py`, `assistant_platform/api/actor.py` (new), `assistant_platform/api/sessions.py`, `assistant_platform/api/prompts.py`, tests |
| Encryption key | `pulse/config.py`, config-load tests |
| JWT | `pulse/web/auth_tokens.py`, `pulse/web/deps.py`, `pulse/web/app.py`, tests |
| Proxy session | `proxy/session.go`, `proxy/mitm.go`, `proxy/pulse_client.go`, `proxy/server.go`, `proxy/main.go`, Go tests |
| DingTalk async | `pulse/channels/dingtalk/mirror.py`, handler call sites, tests |
| Permissions | `pulse/web/permissions.py`, prompt routes, tests |
| Admin password | `pulse/web/portal_auth_api.py`, password helpers, tests |
| Docs/env | `.env.example`, `SECURITY.md`, `docs/RUNBOOK.md`, `docs/ARCHITECTURE.md`, `docs/cursor-usage-api.md`, `CONTRIBUTING.md`, `proxy/README.md`, `config.example.yaml`, `docker/docker-compose.postgres.yml` |

---

### Task 1: Assistant rejects insecure service tokens

**Files:**
- Modify: `assistant_platform/config.py` (`validate_runtime_config`)
- Modify: `pulse/security_tokens.py` (optional: export shared helper usable from assistant, or duplicate `is_insecure_token` check via import from `pulse.security_tokens` — prefer import to stay DRY)
- Test: `tests/assistant_platform/test_runtime_config_security.py` (create)
- Modify: `tests/test_security_tokens.py` if shared API changes

**Produces:** `validate_runtime_config` raises `SystemExit` when `ASSISTANT_SERVICE_TOKEN` is `change-me*`; when `strict=True` or `ASSISTANT_ENV=production`, also reject missing/insecure `ASSISTANT_SECRET_KEY`.

- [ ] **Step 1: Write failing tests** for placeholder token rejection and production secret_key requirement
- [ ] **Step 2: Run tests — expect FAIL**
- [ ] **Step 3: Implement validation** (import `is_insecure_token` from `pulse.security_tokens`)
- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Commit** `sec: reject insecure assistant service tokens at startup`

---

### Task 2: HMAC-sign Pulse→Assistant actor claims

**Problem:** Holding `ASSISTANT_SERVICE_TOKEN` + forging `X-Pulse-Actor-Permissions` grants arbitrary access.

**Design:**
- Shared secret = Assistant service token (already sent as Bearer).
- Pulse BFF adds:
  - `X-Pulse-Actor-Ts`: unix seconds
  - `X-Pulse-Actor-Signature`: hex HMAC-SHA256 over  
    `v1\n{member_id}\n{role}\n{channel_user_id}\n{permissions}\n{ts}`  
    keyed by service token.
- Assistant verifies signature, rejects if skew > 300s or mismatch; **only then** trust permission/role headers. Missing/invalid signature → empty permissions (403 on privileged routes).
- Max skew: 300 seconds.

**Files:**
- Create: `pulse/web/assistant_actor.py` — `sign_actor_headers(token, member_id, role, channel_user_id, permissions) -> dict`
- Create: `assistant_platform/api/actor.py` — `verify_and_build_actor(...) -> ActorContext`
- Modify: `pulse/web/assistant_sessions_api.py`, `assistant_prompts_api.py`, `assistant_skills_api.py`, `assistant_capabilities_api.py` — use shared signer
- Modify: `assistant_platform/api/sessions.py`, `prompts.py` (and any other API using `_actor_dependency`) — verify signature
- Test: `tests/test_assistant_actor_hmac.py`, update `tests/assistant_platform/test_sessions_api.py` etc. to send signed headers

**Helper for tests:**
```python
def signed_actor_headers(token: str, *, member_id="m1", role="owner", channel_user_id="", permissions="assistant:sessions:read:all") -> dict[str, str]:
    ...
```

- [ ] **Step 1: Failing tests** — unsigned headers → 403; signed → ok; tampered perms → 403; expired ts → 403
- [ ] **Step 2: Implement signer + verifier + wire BFF/Assistant**
- [ ] **Step 3: Update existing assistant API tests to use signed headers**
- [ ] **Step 4: Run focused pytest — PASS**
- [ ] **Step 5: Commit** `sec: HMAC-sign Pulse actor claims for Assistant APIs`

---

### Task 3: Fix credential encryption_key env overwrite

**Bug:** `pulse/config.py` unconditionally sets  
`cfg.credentials.encryption_key = env.pulse_credential_encryption_key`  
so an empty env clears yaml value.

**Fix:** Only override when env value is non-empty (same pattern as `pulse_internal_service_token`).

**Files:**
- Modify: `pulse/config.py` (~line 457)
- Test: `tests/test_config_encryption_key.py` (create) or extend existing config tests

```python
# desired
if env.pulse_credential_encryption_key:
    cfg.credentials.encryption_key = env.pulse_credential_encryption_key
```

- [ ] **Step 1: Failing test** — yaml key preserved when env empty; env wins when set
- [ ] **Step 2: Implement**
- [ ] **Step 3: pytest PASS + commit** `fix: do not clear encryption_key with empty env`

---

### Task 4: JWT secret hardening (no silent admin_token fallback in production)

**Files:**
- Modify: `pulse/web/auth_tokens.py` — prefer `jwt_secret`; if missing and `PULSE_ENV=production` / web production flag, raise; else allow `admin_token` fallback with a logged warning once
- Modify: `pulse/web/app.py` — on startup, if using admin_token as JWT secret, warn; reject empty jwt in production if you can detect production via env
- Modify: `pulse/web/deps.py` — keep legacy `admin_token` bearer for disaster recovery but document; do not expand scope
- Test: `tests/test_auth_tokens.py` (create/extend)

**Behavior:**
- Development: `jwt_secret or admin_token` still works (compat).
- Production (`PULSE_ENV=production` or `ASSISTANT_ENV` analogue — use `os.environ.get("PULSE_ENV") == "production"`): require non-empty `JWT_SECRET` / `config.web.jwt_secret`.

- [ ] **Step 1–4: TDD + implement + commit** `sec: require JWT_SECRET in production`

---

### Task 5: Proxy session TTL + re-authorize

**Problem:** After exchange, `SessionMap.Lookup` never re-checks Pulse; revoke unused until restart.

**Design:**
- `SessionBinding` gains `BoundAt time.Time`, `PulseKey string` (already has PulseKey).
- `SessionMap.Lookup` returns binding; caller checks TTL.
- Default session TTL: **5 minutes** (env `PROXY_SESSION_TTL`, flag `-session-ttl`).
- On each MITM business request (or when TTL exceeded): call `pulse.Authorize(binding.PulseKey)`; if not ok → delete binding, 401; if ok → refresh `boundAt`.
- To limit load: re-authorize at most every TTL (lazy refresh), not every request if within TTL — **wait**: audit issue is revoke doesn't apply until restart. So we need either short TTL **or** re-authorize every N seconds. Prefer: **re-authorize when age > sessionTTL**; default TTL 60s–300s. Use **60s** to align with auth cache, or **120s**. Plan default: **120s**.
- Add `SessionMap.Delete(jwt)`.
- Negative auth results for pool keys stay cached 60s (existing); loan_alias uncached (existing).

**Files:**
- Modify: `proxy/session.go`, `proxy/mitm.go`, `proxy/main.go`, `proxy/server.go` if needed
- Test: `proxy/session_test.go`, new reauth tests in `proxy/mitm` or dedicated file

- [ ] **Step 1: Failing test** — binding older than TTL triggers Authorize; suspended → 401 and unbound
- [ ] **Step 2: Implement TTL + re-authorize + Delete**
- [ ] **Step 3: `cd proxy && go test ./...` PASS**
- [ ] **Step 4: Commit** `fix(proxy): re-authorize sessions after TTL`

---

### Task 6: Proxy CONNECT error status + listen/allowlist hardening

**Files:**
- Modify: `proxy/server.go` — fix `writeHTTPError` to emit `HTTP/1.1 {code} {text}\r\n...`
- Modify: `proxy/main.go` / `proxy/config.go` — support `PROXY_LISTEN` (default keep `0.0.0.0:8317` for Docker; document `127.0.0.1:8317` for local); `PROXY_CONNECT_ALLOWLIST` default `*.cursor.sh,cursor.sh` — blind tunnel only for allowlisted hosts; non-matching CONNECT → 403
- Test: `proxy/server` / connect tests
- Modify: `proxy/README.md`, `SECURITY.md` — document listen + allowlist

```go
func writeHTTPError(c net.Conn, status int) {
	text := http.StatusText(status)
	fmt.Fprintf(c, "HTTP/1.1 %d %s\r\nContent-Length: 0\r\n\r\n", status, text)
}
```

- [ ] **Step 1–4: TDD + implement + docs snippet + commit** `fix(proxy): valid CONNECT errors and host allowlist`

**Status:** Done — `writeHTTPError` emits valid status line; `PROXY_LISTEN` / `-listen`; `PROXY_CONNECT_ALLOWLIST` gates CONNECT (403 off-list); tests in `server_test.go` / `allowlist_test.go`; docs updated.

---

### Task 7: Proxy resource limits + exhausted reset

**Files:**
- Modify: `proxy/mitm.go` — reduce non-stream body cap from `1<<30` to e.g. `32<<20` (32 MiB) unless env overrides
- Modify: `proxy/server.go` — set `ReadHeaderTimeout` / `IdleTimeout` on per-conn `http.Server` (e.g. 30s / 120s)
- Modify: `proxy/usage_tap.go` — cap buffer growth (max e.g. 8 MiB then drop/stop tapping)
- Modify: `proxy/pool.go` / `main.go` — periodic `reset()` of exhausted flags every N minutes (env `PROXY_EXHAUSTED_RESET`, default 30m) OR clear exhausted on successful pool hot-update option — prefer timed reset via ticker in main
- Modify: `proxy/README.md` — document exhausted sticky semantics + reset interval
- Tests for limits / reset where practical

- [ ] **Step 1–4: implement + tests + commit** `fix(proxy): timeouts, body caps, exhausted reset`

---

### Task 8: DingTalk mirror non-blocking I/O

**Files:**
- Modify: `pulse/channels/dingtalk/mirror.py` — use `httpx.AsyncClient` + `asyncio.sleep` when called from async handler; keep sync wrapper if needed for sync callers
- Modify: `pulse/channels/dingtalk/handler.py` — `await` async mirror
- Test: extend mirror/handler tests

- [ ] **Step 1–4: TDD + implement + commit** `fix: async DingTalk assistant mirror HTTP`

---

### Task 9: Prompt permission model consistency

**Problem:** JWT still advertises `assistant:prompts:write|approve` but `has_permission` hard-denies; approve route 403 forever.

**Fix (choose minimal):**
- Remove `assistant:prompts:write` and `assistant:prompts:approve` from `ROLE_PERMISSIONS` / `resolve_permissions` output.
- Keep hard-deny in `has_permission` as belt-and-suspenders OR remove if routes already return 410.
- Ensure approve/write routes consistently return **410 Gone** (not 403) with message that prompt write is retired.
- Update tests expecting these permissions in JWT.

**Files:** `pulse/web/permissions.py`, prompt API modules, related tests

- [ ] **Step 1–4: TDD + implement + commit** `fix: align retired prompt write permissions`

---

### Task 10: Hash ADMIN_PASSWORD verification path

**Files:**
- Modify: `pulse/web/portal_auth_api.py` — if `ADMIN_PASSWORD` looks like a hash (prefix) use `passwords.verify`; else `compare_digest` on plaintext for backward compat; prefer documenting hashed form in `.env.example`
- Reuse existing `pulse` password helpers used for members
- Test: portal auth tests

- [ ] **Step 1–4: TDD + implement + commit** `sec: support hashed ADMIN_PASSWORD`

---

### Task 11: Documentation + env alignment

**Files:**
- Modify: `.env.example` — add `PULSE_INTERNAL_TOKEN` (same value note as service token for Assistant→Pulse), `PULSE_BASE_URL`, brief comments
- Modify: `SECURITY.md` — either add re-encrypt steps inline or link to new RUNBOOK section; fix dual-token naming
- Modify: `docs/RUNBOOK.md` — add § credential re-encrypt procedure; fix `init-db` upgrade path (no `--profile tools run init-db`; use oneshot / `pulse init-db`)
- Modify: `CONTRIBUTING.md` — same init-db fix
- Modify: `docker/docker-compose.postgres.yml` comments if still wrong
- Modify: `proxy/README.md` — correct authorize path `/api/internal/v1/proxy/authorize`; `pk_` vs `pka_`; session TTL / allowlist
- Modify: `docs/cursor-usage-api.md` — on-demand now configurable via settings / `enforce_on_demand_disabled`
- Modify: `docs/ARCHITECTURE.md` — dual internal tokens; on-demand settings pointer
- Modify: `config.example.yaml` — note feishu/wecom are stubs
- Modify: `docs/README.md` — optional index links
- Modify: on-demand design status line to “已落地” if present

**Re-encrypt RUNBOOK outline:**
1. Stop writers (web/channel/assistant/proxy).
2. Backup DB.
3. Script or documented Python one-liner: decrypt all credentials with old key, re-encrypt with new, update env, restart.
4. If no script exists, add `pulse rotate-credential-key --old ... --new ...` **only if small**; else document manual SQL/Python steps using `CredentialService`.

Prefer implementing a small CLI `pulse rotate-credential-key` if `CredentialService` already supports decrypt/encrypt — include in this task if <~80 LOC; otherwise document manual steps clearly.

- [ ] **Step 1: Apply doc/env edits**
- [ ] **Step 2: Add rotate CLI or concrete RUNBOOK Python snippet (working)**
- [ ] **Step 3: Commit** `docs: align security, tokens, init-db, on-demand, proxy paths`

---

### Task 12: Smoke verification

- [ ] **Step 1:** `pytest --tb=short -q` (or focused subsets if full suite too long; must run security, config, assistant actor, portal, dingtalk mirror tests)
- [ ] **Step 2:** `cd proxy && go test ./...` (skip with note if Go missing)
- [ ] **Step 3:** Fix any breakage from integration
- [ ] **Step 4:** Commit any fixes `test: stabilize audit remediation suite`
- [ ] **Step 5:** Update `.superpowers/sdd/progress.md` ledger — all tasks complete

---

## Out of scope (explicit defer)

- Extracting `assistant_platform` contracts into a third package
- Multi-worker durable guide-upload / reply-dedupe store (document single-process assumption only if touched)
- Making Secret Store refuse service-token-derived keys outside production beyond existing strict mode
- Public TLS termination for proxy (ops concern)

## Execution notes

- Independent domains for parallel **review** only; implementers run **sequentially** (shared files).
- After all tasks: whole-branch review vs merge-base `master`.
