# Release process

How to cut a versioned GitHub release for `cnwinds/cursor-pulse`. Follow this checklist end-to-end; do not improvise the Release body language or skip CI.

## Hard rules (do not violate)

1. **GitHub Release notes are English-only.** Match the tone and section layout of prior releases (`v0.1.0`, `v0.2.0`): English title body, `### Added` / `### Changed` / `### Fixed` / `### Misc` / `### Known limitations`, English-first README pointer + `README_CN.md` link. **Never** paste the Chinese `CHANGELOG.md` section into `gh release create` / `gh release edit`.
2. **`CHANGELOG.md` stays Chinese** (Keep a Changelog). Repo changelog and GitHub Release body are **two different artifacts**.
3. **Version sources**: bump `[project].version` in [`pyproject.toml`](../../pyproject.toml) and add a dated section in [`CHANGELOG.md`](../../CHANGELOG.md). Tag is `vX.Y.Z` (leading `v`).
4. **CI must be green** before tagging/pushing a release commit. Locally mirror [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml):
   - `pytest --tb=short -q` (use project `.venv`; clear HTTP(S)_PROXY if pip/npm are blocked)
   - `cd proxy && go test ./...`
   - `cd web-admin && npm ci && npm run build`
5. **Do not commit** untracked runtime paths such as `data/`.
6. Prefer **annotated tags**: `git tag -a vX.Y.Z -m "vX.Y.Z"`.

## Checklist

### 1. Prep changelog and version

- Move `[Unreleased]` items into `## [X.Y.Z] - YYYY-MM-DD` in Chinese.
- Update compare links at the bottom of `CHANGELOG.md` (`Unreleased` → `vX.Y.Z`, add `[X.Y.Z]` compare from previous tag).
- Set `version = "X.Y.Z"` in `pyproject.toml`.
- Commit message style (historical): `chore: release vX.Y.Z` with a short why.

### 2. Verify CI locally (same jobs as GitHub)

```bash
source .venv/bin/activate
# Prefer matching CI exactly for the release gate:
pytest --tb=short -q
(cd proxy && go test ./...)
(cd web-admin && npm ci && npm run build)
```

Watch for **date-sensitive fixtures** (e.g. key-loan snapshot `cycle_end` relative to today). Fix failures before tagging.

### 3. Tag and push

```bash
git push origin HEAD          # release commit on master/main
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

Confirm Actions for the release commit: all three jobs `success`  
(`gh run list` / `gh run view`, or the Actions URL).

### 4. GitHub Release (English)

Requires `gh` authenticated (`gh auth status`). If not logged in, run device flow and give the user the one-time code + https://github.com/login/device — do not proceed without a successful `gh auth status`.

Draft the **English** notes from the Chinese changelog (translate; do not paste Chinese). Skeleton (mirror `v0.2.0`):

```markdown
## vX.Y.Z

Self-hosted usage metering and quota control panel for team Cursor accounts. MIT licensed. Chinese docs: [README_CN.md](https://github.com/cnwinds/cursor-pulse/blob/master/README_CN.md).

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Misc
- ...

### Known limitations
- Usage sync relies on undocumented Cursor APIs ... (see docs/cursor-usage-api.md)
- The MITM proxy requires trusting a self-signed CA ... (see proxy/README.md)

**Full changelog**: https://github.com/cnwinds/cursor-pulse/blob/master/CHANGELOG.md
```

Create (or fix) the release:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file /tmp/vX.Y.Z-notes.en.md
# If a Chinese body was published by mistake:
gh release edit vX.Y.Z --notes-file /tmp/vX.Y.Z-notes.en.md
```

Return the release URL to the user.

## Auth notes for agents

- Prefer `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` around `gh` / `curl` to api.github.com when a broken proxy returns 403.
- Device login: keep `gh auth login --hostname github.com --git-protocol https --web` running until the user finishes; print the **current** one-time code (old codes expire).
- Never update `git config` for author identity; use `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env if the environment has no identity (match recent `git log` authors).

## Common failures

| Symptom | Fix |
|--------|-----|
| Release body in Chinese | `gh release edit` with English notes (rule 1) |
| pytest fails on key-loan recommend after a date rollover | Fixture `cycle_end` must be relative to `date.today()` |
| `gh` / Actions API 403 via proxy | Unset proxy env vars for that command |
| `gh release create` unauthorized | Complete device auth; verify `gh auth status` |
| Tag exists but no Release page | Tag alone is not a release — run `gh release create` |
