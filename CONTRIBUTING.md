# Contributing

Thanks for contributing to Cursor Pulse.

## Prerequisites

- Python ≥ 3.11
- Node.js 20+ (only if you touch `web-admin/`)
- Go 1.22+ (only if you touch `proxy/`)

## Local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"
copy config.example.yaml config.yaml   # or cp on Unix
copy .env.example .env                 # fill DingTalk / JWT as needed
pulse init-db
```

Minimal UI/API loop:

```bash
# Windows
.\cursor-pulse.bat start web admin
# macOS/Linux
./cursor-pulse.sh start web admin
```

Full local stack (channel + assistant + optional proxy): see root `README.md` and `docker/README.md`.

## Tests (PR gate)

```bash
pytest --tb=short -q
```

If you change Go code:

```bash
cd proxy && go test ./...
```

If you change the admin UI:

```bash
cd web-admin && npm ci && npm run build
```

## Docker

Always work under `docker/` (see `docker/README.md`). Do **not** use root-level `docker compose` — those files were removed.

```bash
cd docker
./scripts/setup.sh    # or bash scripts/setup.sh on Windows Git Bash
docker compose build
docker compose --profile tools run --rm init-db
docker compose up -d
```

## Commits & reviews

- Prefer [Conventional Commits](https://www.conventionalcommits.org/): `feat`, `fix`, `chore`, `docs`, `refactor`, `test`.
- Keep PRs focused; link issues when applicable.
- Do not commit secrets, databases, CA keys, or personal paths/emails.

## Security

See [SECURITY.md](SECURITY.md). Never paste production tokens into issues or PRs.
