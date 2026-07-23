# Security Policy

## Supported versions

Security fixes target the default branch (`master` / `main`) of the public repository. Tagged releases will be listed here as they appear.

## Threat model (summary)

Cursor Pulse is a **self-hosted** control plane. Sensitive assets include:

- Cursor / vendor API keys and proxy pool credentials
- DingTalk app secrets
- JWT / internal service tokens
- Optional MITM CA private key for the Go data-plane proxy

Trust boundaries: Web Admin, Internal HTTP APIs, DingTalk channel, Assistant service, optional Go MITM proxy.

**Non-goals:** integrity when clients use `-k` / skip TLS verification against the local MITM CA.

## Reporting a vulnerability

Please **do not** open a public issue with secrets or exploit details.

Prefer GitHub [Security Advisories](https://github.com/cnwinds/cursor-pulse/security/advisories/new) on the public repository, or email the maintainer listed in `pyproject.toml` / GitHub profile.

Include: affected version/commit, impact, reproduction steps, and whether you plan a coordinated disclosure.

## Secret handling

- Copy `.env.example` → `.env` and `config.example.yaml` → `config.yaml`. **Never commit** `.env`, `config.yaml`, `*.db`, `*.pem`, `*.key`, or `.dev/` dumps.
- Generate high-entropy values for: `JWT_SECRET`, `PULSE_CREDENTIAL_ENCRYPTION_KEY`, `PULSE_INTERNAL_SERVICE_TOKEN`, `ASSISTANT_SERVICE_TOKEN`, `ASSISTANT_SECRET_KEY`.
- Reject placeholder tokens such as `change-me-*` in production (see `docker/scripts/setup.sh`).
- Rotate secrets after any accidental leak; re-encrypt stored credentials after changing `PULSE_CREDENTIAL_ENCRYPTION_KEY` (see `docs/RUNBOOK.md`).

## AuthZ notes

- Internal routes (`/api/internal/v1/*`) require a service token; misconfiguration must fail closed.
- Settings “reveal” endpoints expose plaintext secrets — treat admin roles carefully.
- `ADMIN_PASSWORD` / `ADMIN_WEB_TOKEN` are disaster-recovery paths; prefer portal bootstrap accounts.

## Deployment hardening

- Do not expose web or proxy listeners to the public Internet without TLS termination and network controls.
- Keep MITM CA keys only on trusted operator machines; rotate if leaked.
- `PROXY_DEBUG_USAGE` dumps request bodies — local debugging only; never commit debug trees.
