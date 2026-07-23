from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.web.deps import PortalUser


class CreateAssignmentBody(BaseModel):
    team_id: str | None = None
    scope_type: str
    scope_id: str = ""
    pack_id: str | None = None
    capability_key: str | None = None
    capability_version: str | None = None


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


def register_assistant_capabilities_routes(
    app,
    get_db,
    require_capability,
    team_repo_fn,
    config: AppConfig,
) -> None:
    @app.get(
        "/api/v2/assistant/capabilities/packs",
        dependencies=[Depends(require_capability("assistant:capabilities:read"))],
    )
    def assistant_capabilities_packs(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:capabilities:read")),
    ):
        team, _repo = team_repo_fn(session)
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/capabilities/packs",
            params={"team_id": team.id},
        )

    @app.get(
        "/api/v2/assistant/capabilities/catalog",
        dependencies=[Depends(require_capability("assistant:capabilities:read"))],
    )
    def assistant_capabilities_catalog(
        user: PortalUser = Depends(require_capability("assistant:capabilities:read")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/capabilities/catalog",
        )

    @app.get(
        "/api/v2/assistant/capabilities/assignments",
        dependencies=[Depends(require_capability("assistant:capabilities:read"))],
    )
    def assistant_capabilities_assignments(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:capabilities:read")),
    ):
        team, _repo = team_repo_fn(session)
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/capabilities/assignments",
            params={"team_id": team.id},
        )

    @app.post(
        "/api/v2/assistant/capabilities/assignments",
        dependencies=[Depends(require_capability("assistant:capabilities:write"))],
    )
    def assistant_capabilities_create_assignment(
        body: CreateAssignmentBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:capabilities:write")),
    ):
        team, _repo = team_repo_fn(session)
        payload = body.model_dump(exclude_none=True)
        payload["team_id"] = team.id
        return _proxy_assistant(
            config,
            user=user,
            method="POST",
            path="/api/assistant/v1/capabilities/assignments",
            json_body=payload,
        )

    @app.delete(
        "/api/v2/assistant/capabilities/assignments/{assignment_id}",
        dependencies=[Depends(require_capability("assistant:capabilities:write"))],
    )
    def assistant_capabilities_delete_assignment(
        assignment_id: str,
        user: PortalUser = Depends(require_capability("assistant:capabilities:write")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="DELETE",
            path=f"/api/assistant/v1/capabilities/assignments/{assignment_id}",
        )

    @app.get(
        "/api/v2/assistant/capabilities/members/{member_id}/resolved",
        dependencies=[Depends(require_capability("assistant:capabilities:read"))],
    )
    def assistant_capabilities_member_resolved(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:capabilities:read")),
        role: Annotated[str | None, Query()] = None,
        channel: str = Query("dingtalk"),
    ):
        team, _repo = team_repo_fn(session)
        params: dict[str, Any] = {
            "team_id": team.id,
            "member_id": member_id,
            "channel": channel,
        }
        if role:
            params["role"] = role
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/capabilities/me",
            params=params,
        )
