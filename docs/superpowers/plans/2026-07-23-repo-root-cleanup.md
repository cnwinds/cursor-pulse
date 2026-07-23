# Repo Root Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean the repository root (junk + duplicate Docker), delete `samples/`/`review/`, relocate cursor-usage docs/scripts, and keep tests green without Cursor CSV sample fixtures.

**Architecture:** File-system and documentation cleanup only. Non-Cursor `manual_csv` / parsers / DingTalk file ingest stay. Tests that used `samples/usage-events-sample.csv` either drop sample-contract assertions or seed via tiny inline CSV / direct DB writes.

**Tech Stack:** git, pytest, existing Pulse CSV parser (`EXPECTED_HEADERS`), Markdown docs.

**Spec:** [2026-07-23-repo-root-cleanup-design.md](../specs/2026-07-23-repo-root-cleanup-design.md)

---

## File map

| Action | Path |
|--------|------|
| Delete | `=`, root `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker-compose.postgres.yml` |
| Delete | `samples/`, `review/`, tracked `.superpowers/sdd/task-5-report.md` |
| Move | `cursor-usage-api.md` → `docs/cursor-usage-api.md` |
| Move | `cursor-usage.sh` → `scripts/cursor-usage.sh` |
| Update | `README.md`, `docs/RUNBOOK.md`, `docs/PRD.md`, live links to cursor-usage paths |
| Rewrite/delete | tests listed in spec that reference `samples/` |

---

### Task 1: Delete junk + root Docker duplicates + review/samples + .superpowers track

**Files:**
- Delete: `=`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker-compose.postgres.yml`, `samples/usage-events-sample.csv`, `review/20260721-project-architecture.md`, `.superpowers/sdd/task-5-report.md`

- [ ] **Step 1: Remove paths from git index and disk**

```powershell
git rm -f -- "=" Dockerfile .dockerignore docker-compose.yml docker-compose.postgres.yml
git rm -rf samples review
git rm -f -- ".superpowers/sdd/task-5-report.md" 2>$null
```

- [ ] **Step 2: Verify gone from index**

```powershell
git ls-files | Select-String -Pattern '^(=|Dockerfile|\.dockerignore|docker-compose|samples/|review/|\.superpowers/)'
```

Expected: no matches (except nothing).

- [ ] **Step 3: Commit**

```powershell
git commit -m "chore: remove junk files, root docker duplicates, samples, and review"
```

---

### Task 2: Relocate cursor-usage doc and script

**Files:**
- Move: `cursor-usage-api.md` → `docs/cursor-usage-api.md`
- Move: `cursor-usage.sh` → `scripts/cursor-usage.sh`
- Modify: `docs/cursor-usage-api.md` (script path line)
- Modify: `docs/plans/2026-07-13-billing-cycle-quota-key-allocation.md` (link)

- [ ] **Step 1: Create scripts dir and git-mv**

```powershell
New-Item -ItemType Directory -Force -Path scripts | Out-Null
git mv cursor-usage-api.md docs/cursor-usage-api.md
git mv cursor-usage.sh scripts/cursor-usage.sh
```

- [ ] **Step 2: Fix script path inside the moved doc**

In `docs/cursor-usage-api.md`, change any `tools/cursor-usage.sh` or root `cursor-usage.sh` references to `scripts/cursor-usage.sh`.

- [ ] **Step 3: Fix live doc link**

In `docs/plans/2026-07-13-billing-cycle-quota-key-allocation.md`, change `../../cursor-usage-api.md` to `../cursor-usage-api.md`.

- [ ] **Step 4: Commit**

```powershell
git commit -m "chore: move cursor-usage doc and script under docs/ and scripts/"
```

---

### Task 3: Update README / RUNBOOK / PRD

**Files:**
- Modify: `README.md`
- Modify: `docs/RUNBOOK.md`
- Modify: `docs/PRD.md`

- [ ] **Step 1: README — remove samples parse/import from quick start; point Docker to `docker/`**

Remove lines like:

```bash
pulse parse samples/usage-events-sample.csv
pulse import samples/usage-events-sample.csv --user-id u1 --name Alice --period 2026-06
```

Ensure any Docker section says work from `docker/` (compose files there). Keep `cursor-pulse.bat` / `.sh` at root in docs.

- [ ] **Step 2: RUNBOOK — remove samples paths**

Replace or delete `pulse import samples/...` examples; if an import example remains, use a placeholder path like `/path/to/usage.csv` for non-Cursor CSV.

- [ ] **Step 3: PRD — remove samples tree / verified-sample path references**

Update directory tree and「已验证样本」lines so they do not point at deleted `samples/`.

- [ ] **Step 4: Commit**

```powershell
git commit -m "docs: drop samples paths; point Docker docs to docker/"
```

---

### Task 4: Fix tests — drop sample contracts; mini fixtures for logic tests

**Files:**
- Modify or rewrite: all under `tests/` matching `samples/` or `usage-events-sample`
- Optional helper: `tests/fixtures/mini_usage_events.csv` OR inline CSV string in a shared test helper

**Mini CSV** (must match `EXPECTED_HEADERS` in `pulse/extract/csv_parser.py`):

```csv
Date,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
2026-06-01T12:00:00.000Z,chat,gpt-4o,No,0,10,0,5,15,Included
2026-06-02T12:00:00.000Z,chat,gpt-4o,No,0,20,0,10,30,$0.01
```

(Adjust columns if parser requires Cloud Agent ID / Automation ID as optional — omit optional headers.)

- [ ] **Step 1: List remaining references**

```powershell
rg -n "samples/|usage-events-sample" tests pulse docs/README.md README.md docs/RUNBOOK.md docs/PRD.md
```

- [ ] **Step 2: For each test file**

- Pure contract tests (498 rows, fixed cost buckets) → delete those test functions or whole file if empty.
- Logic tests → point `SAMPLE` at `tests/fixtures/mini_usage_events.csv` (create file) or write temp CSV in fixture.

- [ ] **Step 3: Run pytest**

```powershell
pytest --tb=short -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```powershell
git commit -m "test: replace Cursor sample CSV fixtures with mini fixtures"
```

---

### Task 5: Final verification

- [ ] **Step 1: Acceptance rg**

```powershell
git ls-files | Select-String -Pattern '^(=|Dockerfile$|\.dockerignore$|docker-compose|samples/|review/|\.superpowers/)'
rg -n "samples/|usage-events-sample" --glob '!docs/superpowers/**' --glob '!docs/plans/**'
```

Live docs under `docs/superpowers` may still mention samples historically — OK per spec. `README.md` / `RUNBOOK` / `PRD` must be clean.

- [ ] **Step 2: pytest again**

```powershell
pytest --tb=short -q
```

- [ ] **Step 3: Done** — hand off to finishing-a-development-branch if partner wants PR/merge options.

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| Delete `=` and root Docker | Task 1 |
| Delete review/samples/.superpowers track | Task 1 |
| Move cursor-usage.* | Task 2 |
| Doc sync README/RUNBOOK/PRD | Task 3 |
| Keep launchers at root | (no move) |
| Keep manual_csv capability | (no delete of pulse extract) |
| Tests green without samples | Task 4–5 |
