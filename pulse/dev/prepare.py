"""Prepare local (mode-1) workspace before `pulse dev start`."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pulse.dev.services import ensure_proxy_binary, npm_executable, project_root


_HOST_OVERRIDES = {
    "ASSISTANT_MIRROR_BASE_URL": "http://127.0.0.1:8090",
    "PULSE_BASE_URL": "http://127.0.0.1:8080",
}

_LOOPBACK_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
)

_DINGTALK_REQUIRED = ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET")


def _primary_lan_ipv4() -> str | None:
    """Best-effort LAN IPv4 for CORS/OAuth when browsing via host IP (not loopback)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
    except OSError:
        return None
    if not host or host.startswith("127."):
        return None
    return host


def _default_cors_origins() -> tuple[str, ...]:
    origins = list(_LOOPBACK_CORS_ORIGINS)
    lan = _primary_lan_ipv4()
    if lan:
        origins.extend((f"http://{lan}:5173", f"http://{lan}:8080"))
    return tuple(origins)


def _parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, val = raw.partition("=")
            values[key.strip()] = val.strip().strip("'").strip('"')
        return values
    out: dict[str, str] = {}
    for key, value in dotenv_values(path).items():
        if key:
            out[key] = "" if value is None else str(value)
    return out


def _write_env(path: Path, values: dict[str, str], *, preferred_order: list[str]) -> None:
    seen: set[str] = set()
    lines: list[str] = [
        "# Local / mode-1 environment (managed by cursor-pulse.sh / pulse.dev.prepare)",
        "# Secrets come from docker/.env when present; host URLs are overridden for loopback.",
        "",
    ]
    for key in preferred_order:
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
    for key in sorted(values):
        if key not in seen:
            lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_data_link(root: Path) -> None:
    """Prefer docker/data (production bind-mount) when present."""
    docker_data = root / "docker" / "data"
    data = root / "data"
    docker_db = docker_data / "pulse.db"
    if not docker_db.is_file():
        data.mkdir(parents=True, exist_ok=True)
        print(f"[prepare] data/ → {data} (no docker/data/pulse.db)")
        return

    if data.is_symlink():
        if data.resolve() == docker_data.resolve():
            print(f"[prepare] data/ already → {docker_data}")
            return
        data.unlink()
    elif data.is_dir():
        local_db = data / "pulse.db"
        # Avoid clobbering a large local DB; only replace empty/small scratch DBs.
        if local_db.is_file() and local_db.stat().st_size > 1_000_000:
            print(
                f"[prepare] keep existing data/pulse.db "
                f"({local_db.stat().st_size} bytes); not linking docker/data"
            )
            return
        from datetime import datetime

        bak = root / f"data.scratch-bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        data.rename(bak)
        print(f"[prepare] moved scratch data/ → {bak.name}")
    elif data.exists():
        data.unlink()

    data.symlink_to("docker/data")
    print(f"[prepare] data/ → docker/data")


def ensure_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    docker_env = root / "docker" / ".env"
    example = root / ".env.example"

    merged = _parse_env(example)
    merged.update(_parse_env(docker_env))
    existing = _parse_env(env_path)
    # Keep user-edited values (e.g. DingTalk keys added to root .env)
    merged.update({k: v for k, v in existing.items() if v.strip()})

    merged.update(_HOST_OVERRIDES)

    cors = {
        part.strip()
        for part in (merged.get("WEB_CORS_ORIGINS") or "").split(",")
        if part.strip()
    }
    cors.update(_default_cors_origins())
    merged["WEB_CORS_ORIGINS"] = ",".join(sorted(cors))

    preferred = []
    for source in (example, docker_env, env_path):
        for key in _parse_env(source):
            if key not in preferred:
                preferred.append(key)
    for key in _HOST_OVERRIDES:
        if key not in preferred:
            preferred.append(key)
    if "WEB_CORS_ORIGINS" not in preferred:
        preferred.append("WEB_CORS_ORIGINS")

    _write_env(env_path, merged, preferred_order=preferred)
    print(f"[prepare] wrote {env_path}")
    return merged


def ensure_web_listen_all(root: Path) -> None:
    """Ensure config.yaml web.host is 0.0.0.0 for LAN access."""
    path = root / "config.yaml"
    if not path.is_file():
        example = root / "config.example.yaml"
        docker_cfg = root / "docker" / "config.yaml"
        if docker_cfg.is_file():
            shutil.copy(docker_cfg, path)
            print(f"[prepare] copied docker/config.yaml → config.yaml")
        elif example.is_file():
            shutil.copy(example, path)
            print(f"[prepare] copied config.example.yaml → config.yaml")
        else:
            print("[prepare] warn: no config.yaml found")
            return

    text = path.read_text(encoding="utf-8")
    if re.search(r"^web:\s*$", text, re.M):
        if re.search(r"^web:\s*\n(?:[ \t].*\n)*[ \t]+host:", text, re.M):
            text2 = re.sub(
                r"(^web:\s*\n(?:[ \t].*\n)*?[ \t]+host:\s*)[\"']?[^\"'\n]+[\"']?",
                r'\1"0.0.0.0"',
                text,
                count=1,
                flags=re.M,
            )
            if text2 != text:
                path.write_text(text2, encoding="utf-8")
                print('[prepare] config.yaml web.host → "0.0.0.0"')
            else:
                print("[prepare] config.yaml web.host already set")
            return
        text = re.sub(
            r"(^web:\s*\n)",
            r'\1  host: "0.0.0.0"\n  port: 8080\n',
            text,
            count=1,
            flags=re.M,
        )
        path.write_text(text, encoding="utf-8")
        print('[prepare] injected web.host "0.0.0.0" into config.yaml')
        return

    path.write_text(text + '\nweb:\n  host: "0.0.0.0"\n  port: 8080\n', encoding="utf-8")
    print('[prepare] appended web.host "0.0.0.0" to config.yaml')


def ensure_admin_node_modules(root: Path) -> None:
    web_admin = root / "web-admin"
    if not (web_admin / "package.json").is_file():
        return
    if (web_admin / "node_modules").is_dir():
        print("[prepare] web-admin/node_modules ok")
        return
    npm = npm_executable()
    print("[prepare] npm install (web-admin)…")
    subprocess.run([npm, "install"], cwd=str(web_admin), check=True)


def ensure_proxy(root: Path) -> None:
    try:
        binary = ensure_proxy_binary(root)
        print(f"[prepare] proxy binary: {binary}")
    except FileNotFoundError as exc:
        print(f"[prepare] warn: proxy unavailable ({exc})")


def stop_docker_port_conflicts(root: Path) -> None:
    """Stop compose services that would steal local ports."""
    compose = root / "docker" / "docker-compose.yml"
    if not compose.is_file() or not shutil.which("docker"):
        return
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "ps", "-q", "web", "assistant"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not ids:
        # Also stop proxy compose if listening
        proxy_compose = root / "docker" / "docker-compose.proxy.yml"
        if proxy_compose.is_file():
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(proxy_compose),
                    "stop",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        print("[prepare] no conflicting docker web/assistant containers")
        return
    print("[prepare] stopping docker web/assistant to free ports…")
    subprocess.run(
        ["docker", "compose", "-f", str(compose), "stop", "web", "assistant", "channel"],
        check=False,
    )
    proxy_compose = root / "docker" / "docker-compose.proxy.yml"
    if proxy_compose.is_file():
        subprocess.run(
            ["docker", "compose", "-f", str(proxy_compose), "stop"],
            check=False,
            capture_output=True,
            text=True,
        )


def dingtalk_ready(env: dict[str, str]) -> bool:
    return all((env.get(k) or "").strip() for k in _DINGTALK_REQUIRED)


def prepare(*, stop_docker: bool = True) -> dict[str, str]:
    root = project_root()
    os.chdir(root)
    print(f"[prepare] root={root}")
    if stop_docker:
        stop_docker_port_conflicts(root)
    ensure_data_link(root)
    env = ensure_env_file(root)
    ensure_web_listen_all(root)
    ensure_admin_node_modules(root)
    ensure_proxy(root)
    if dingtalk_ready(env):
        print("[prepare] DingTalk credentials: ok (.env)")
    else:
        # DB team_settings.dingtalk can still supply keys via load_config().
        try:
            from pulse.config import load_config

            cfg = load_config(str(root / "config.yaml"))
            if (cfg.dingtalk.app_key or "").strip() and (
                cfg.dingtalk.app_secret or ""
            ).strip():
                print("[prepare] DingTalk credentials: ok (team_settings / DB)")
                return env
        except Exception:
            pass
        missing = [k for k in _DINGTALK_REQUIRED if not (env.get(k) or "").strip()]
        print(
            "[prepare] warn: DingTalk credentials missing in .env "
            f"({', '.join(missing)}) and not found in DB — "
            "channel will be skipped until set"
        )
    return env


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stop_docker = "--keep-docker" not in argv
    prepare(stop_docker=stop_docker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
