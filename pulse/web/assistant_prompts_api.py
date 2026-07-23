from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.web.deps import PortalUser
from pulse.web.permissions import resolve_permissions

_PROMPT_EDITING_RETIRED_DETAIL = (
    "Prompt editing retired; edit files in assistant_platform/prompts/docs"
)

def _gone() -> None:
    raise HTTPException(status_code=410, detail=_PROMPT_EDITING_RETIRED_DETAIL)


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
    permissions = ",".join(sorted(resolve_permissions(user.member)))
    return {
        "Authorization": f"Bearer {token}",
        "X-Assistant-Token": token,
        "X-Pulse-Actor-Member-Id": user.member.id,
        "X-Pulse-Actor-Role": user.member.portal_role or "",
        "X-Pulse-Actor-Channel-User-Id": user.member.dingtalk_user_id,
        "X-Pulse-Actor-Permissions": permissions,
        "Content-Type": "application/json",
    }


def _proxy_assistant(
    config: AppConfig,
    *,
    user: PortalUser,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    _ensure_mirror_config(config)
    mirror = config.assistant_mirror
    url = f"{mirror.base_url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=mirror.timeout_seconds) as client:
            response = client.request(
                method,
                url,
                headers=_assistant_headers(config, user),
                params=params,
                json=json_body,
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


def register_assistant_prompts_routes(
    app,
    get_db,
    require_capability,
    team_repo_fn,
    config: AppConfig,
) -> None:
    @app.get("/api/v2/assistant/prompts")
    def assistant_prompts_list(
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/prompts",
        )

    @app.get("/api/v2/assistant/prompts/preview")
    def assistant_prompts_preview(
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/prompts/preview",
        )

    @app.post("/api/v2/assistant/prompts/fragments")
    def assistant_prompt_fragment_create():
        _gone()

    @app.get("/api/v2/assistant/prompts/releases")
    def assistant_prompt_releases_list(
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/prompts/releases",
        )

    @app.post("/api/v2/assistant/prompts/releases")
    def assistant_prompt_release_create():
        _gone()

    @app.get("/api/v2/assistant/prompts/releases/{release_id}")
    def assistant_prompt_release_detail(
        release_id: str,
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path=f"/api/assistant/v1/prompts/releases/{release_id}",
        )

    @app.get("/api/v2/assistant/prompts/clusters")
    def assistant_prompt_clusters_list(
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/prompts/clusters",
        )

    @app.get("/api/v2/assistant/prompts/proposals")
    def assistant_prompt_proposals_list(
        user: PortalUser = Depends(require_capability("assistant:prompts:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/prompts/proposals",
        )

    @app.post("/api/v2/assistant/prompts/proposals/{proposal_id}/approve")
    def assistant_prompt_proposal_approve(
        proposal_id: str,
        user: PortalUser = Depends(require_capability("assistant:prompts:approve")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="POST",
            path=f"/api/assistant/v1/prompts/proposals/{proposal_id}/approve",
        )

    @app.post("/api/v2/assistant/prompts/releases/{release_id}/canary")
    def assistant_prompt_release_canary(release_id: str):
        _gone()

    @app.post("/api/v2/assistant/prompts/releases/{release_id}/promote")
    def assistant_prompt_release_promote(release_id: str):
        _gone()

    @app.post("/api/v2/assistant/prompts/releases/{release_id}/rollback")
    def assistant_prompt_release_rollback(release_id: str):
        _gone()
