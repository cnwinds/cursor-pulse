from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, HTTPException

from pulse.config import AppConfig
from pulse.web.deps import PortalUser


def _ensure_mirror_config(config: AppConfig) -> None:
    mirror = config.assistant_mirror
    if not (mirror.base_url or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Assistant 服务未配置，请设置 assistant_mirror.base_url",
        )
    if not (mirror.service_token or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Assistant 服务未配置，请设置 assistant_mirror.service_token",
        )


def _assistant_headers(config: AppConfig, user: PortalUser) -> dict[str, str]:
    mirror = config.assistant_mirror
    token = mirror.service_token.strip()
    return {
        "Authorization": f"Bearer {token}",
        "X-Assistant-Token": token,
        "X-Pulse-Actor-Member-Id": user.member.id,
        "X-Pulse-Actor-Role": user.member.portal_role or "",
        "Content-Type": "application/json",
    }


def _proxy_assistant(
    config: AppConfig,
    *,
    user: PortalUser,
    path: str,
) -> Any:
    _ensure_mirror_config(config)
    mirror = config.assistant_mirror
    url = f"{mirror.base_url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=mirror.timeout_seconds) as client:
            response = client.request(
                "GET",
                url,
                headers=_assistant_headers(config, user),
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Assistant 服务不可达：{exc}",
        ) from exc

    if response.status_code >= 400:
        detail: str
        try:
            payload = response.json()
            if isinstance(payload, dict) and "detail" in payload:
                detail = str(payload["detail"])
            else:
                detail = response.text or f"Assistant 返回 {response.status_code}"
        except Exception:
            detail = response.text or f"Assistant 返回 {response.status_code}"
        raise HTTPException(status_code=response.status_code, detail=detail)

    if not response.content:
        return {}
    return response.json()


def register_assistant_skills_routes(
    app,
    get_db,
    require_capability,
    team_repo_fn,
    config: AppConfig,
) -> None:
    del get_db, team_repo_fn

    @app.get(
        "/api/v2/assistant/skills",
        dependencies=[Depends(require_capability("assistant:skills:read"))],
    )
    def assistant_skills_list(
        user: PortalUser = Depends(require_capability("assistant:skills:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            path="/api/assistant/v1/skills",
        )

    @app.get(
        "/api/v2/assistant/skills/help-topics",
        dependencies=[Depends(require_capability("assistant:skills:read"))],
    )
    def assistant_skills_help_topics(
        user: PortalUser = Depends(require_capability("assistant:skills:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            path="/api/assistant/v1/skills/help-topics",
        )

    @app.get(
        "/api/v2/assistant/skills/{skill_id:path}",
        dependencies=[Depends(require_capability("assistant:skills:read"))],
    )
    def assistant_skills_detail(
        skill_id: str,
        user: PortalUser = Depends(require_capability("assistant:skills:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            path=f"/api/assistant/v1/skills/{skill_id}",
        )
