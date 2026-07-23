# Cursor Pulse

Self-hosted **AI tool usage metering and quota control plane** (DingTalk-first): sync Cursor usage via API Key, manage accounts / loans / alerts, optional Assistant skills, and an optional Go MITM proxy data-plane.

> **License:** [MIT](LICENSE) · **Security:** [SECURITY.md](SECURITY.md) · **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)

## What you get

| Layer | Role |
|-------|------|
| **Pulse** (`pulse/`) | Control plane: DB, DingTalk channel, Web API, internal provider APIs |
| **Assistant** (`assistant_platform/`) | Optional conversation / capabilities / memory service |
| **Admin UI** (`web-admin/`) | Vue portal (dev server or built into Pulse web) |
| **Proxy** (`proxy/`) | Optional Go HTTPS MITM for Cursor traffic + usage tap |

Cursor usage is intended to sync via **API Key**, not CSV. Manual CSV/XLSX remains for non-Cursor tools.

## Quick start (local)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"

cp config.example.yaml config.yaml   # Windows: copy ...
cp .env.example .env                 # fill DINGTALK_* / JWT_SECRET / tokens

pulse init-db
pytest --tb=short -q
```

Start a minimal stack:

```bash
# Windows
.\cursor-pulse.bat start web admin
# macOS/Linux
./cursor-pulse.sh start web admin
```

- API: `http://127.0.0.1:8080`
- Admin UI (Vite): `http://127.0.0.1:5173` after `cd web-admin && npm install && npm run dev`

Full channel + assistant (+ proxy) wiring: [docs/bot-commands.md](docs/bot-commands.md), [docs/RUNBOOK.md](docs/RUNBOOK.md), [proxy/README.md](proxy/README.md).

### DingTalk (high level)

1. Create a DingTalk **企业内部应用** (this is a DingTalk app type, not “this company’s private code”).
2. Enable robot + Stream mode; set `DINGTALK_APP_KEY` / `SECRET` / `ROBOT_CODE` / admin user ids in `.env`.
3. Run `pulse channel` (or `cursor-pulse start channel`).

## Docker

Production compose lives under **`docker/`** only:

```bash
cd docker
./scripts/setup.sh          # creates .env / config.yaml; generates service tokens
# edit docker/.env — never leave empty JWT / encryption keys
docker compose build
docker compose --profile tools run --rm init-db
docker compose up -d
```

Details: [docker/README.md](docker/README.md).

## Docs

| Doc | Audience |
|-----|----------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributors |
| [SECURITY.md](SECURITY.md) | Vulnerability reports & secret handling |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes & API surfaces |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Operators |
| [docs/bot-commands.md](docs/bot-commands.md) | DingTalk commands |
| [docs/cursor-usage-api.md](docs/cursor-usage-api.md) | Unofficial Cursor API notes (may break; use at your own risk) |
| [proxy/README.md](proxy/README.md) | MITM proxy (CA / ToS risk) |

Internal design history lives under `docs/superpowers/` (not required for deploying).

## Risk notice

The optional proxy performs **TLS MITM** for `*.cursor.sh` with a local CA. Unofficial Cursor HTTP APIs may change without notice. Review your org’s policy and Cursor terms before production use.

## License

[MIT](LICENSE) © 2026 xiongbo
