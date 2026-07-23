# Architecture

Cursor Pulse is a **self-hosted monorepo** with three runtimes and optional data-plane.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  web-admin  │────▶│  Pulse web API   │◀────│  DingTalk   │
│  (Vue SPA)  │     │  + channel bot   │     │  Stream     │
└─────────────┘     └────────┬─────────┘     └─────────────┘
                             │ internal HTTP
                    ┌────────▼─────────┐
                    │ Assistant service│  (optional process)
                    └──────────────────┘
                             ▲
                    ┌────────┴─────────┐
                    │ Go MITM proxy    │  (optional data-plane)
                    │ cursor-pulse-proxy│
                    └──────────────────┘
```

## Processes

| Process | How to start | Default | Responsibility |
|---------|--------------|---------|----------------|
| Pulse web | `pulse web` | `:8080` | Portal JWT API, internal provider APIs, static admin |
| Channel | `pulse channel` | Stream client | DingTalk ingress, reminders, capability bridge |
| Assistant | `python -m assistant_platform serve` | `:8090` | Sessions, skills, capability invoke (when mirrored) |
| Admin UI (dev) | `npm run dev` in `web-admin/` | `:5173` | Vue portal against Pulse web |
| Proxy (opt.) | `go run` / `cursor-pulse.bat start proxy` | `:8317` | HTTPS MITM + usage tap → Pulse internal proxy APIs |

Local helper: `cursor-pulse.bat` / `.sh` / `.ps1` wraps `pulse dev` for multi-process start/stop.

## Databases

| File / URL | Owner |
|------------|--------|
| `data/pulse.db` (or `DATABASE_URL`) | Pulse control plane |
| `data/assistant.db` (`ASSISTANT_DATABASE_URL`) | Assistant platform |

## Ingestion model

- **Cursor:** bind User API Key → scheduled / on-demand API sync (`pulse/ingestion/adapters/cursor_api.py`).
- **Other tools:** manual CSV/XLSX / vision / text via channel (not the primary Cursor path).

## HTTP surfaces (supported)

**Portal / public (JWT or portal auth)**

- `/api/auth/*`, `/api/v2/*` (accounts, credentials, quota, loans, assistant proxies, …)
- `/health`

**Internal (service token — fail closed if unset)**

- `/api/internal/v1/capabilities/*`
- `/api/internal/v1/channel/reply`
- `/api/internal/v1/proxy/{authorize,pool,usage,events}`

**Assistant**

- `/api/assistant/v1/*` (events, capabilities, sessions, …); production usually via Pulse portal proxy.

Legacy `/api/*` (non-v2) routes may still exist — prefer v2 for new clients.

## Package layout

| Path | Notes |
|------|--------|
| `pulse/` | Control plane Python package |
| `assistant_platform/` | Assistant package (same wheel today; process-separated) |
| `proxy/` | Go module (not in Docker image by default) |
| `web-admin/` | Vue admin |
| `docker/` | Canonical compose + Dockerfile |
| `scripts/` | Helper scripts (e.g. `cursor-usage.sh`) |
| `docs/superpowers/` | Internal design history — not required to deploy |

Pulse and Assistant currently **import each other** in places; treat them as one product boundary until HTTP-only contracts are finished.

## Config

- `config.yaml` — non-secret structure (from `config.example.yaml`)
- `.env` — secrets and feature flags (from `.env.example`)
- Docker: use `docker/.env` + `docker/config.yaml` after `docker/scripts/setup.sh`

Never commit real secrets. Placeholder tokens like `change-me-*` are rejected at Pulse web startup.
