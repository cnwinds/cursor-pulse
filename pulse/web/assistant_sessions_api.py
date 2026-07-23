from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.web.deps import PortalUser
from pulse.web.permissions import resolve_permissions
from sqlalchemy import select


def _member_display_names(
    session: Session,
    *,
    team_id: str,
    channel_user_ids: set[str],
) -> dict[str, str]:
    if not channel_user_ids:
        return {}
    members = session.scalars(
        select(Member).where(
            Member.team_id == team_id,
            Member.dingtalk_user_id.in_(channel_user_ids),
        )
    ).all()
    return {member.dingtalk_user_id: member.display_name for member in members}


def _enrich_session_item(
    item: dict[str, Any],
    *,
    names_by_user_id: dict[str, str],
) -> dict[str, Any]:
    user_id = item.get("user_id")
    display_name = names_by_user_id.get(user_id) if user_id else None
    enriched = dict(item)
    enriched["user_display_name"] = display_name or user_id or ""
    return enriched


def _enrich_sessions_payload(
    session: Session,
    *,
    team_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        return payload
    user_ids = {str(item.get("user_id")) for item in items if item.get("user_id")}
    names = _member_display_names(session, team_id=team_id, channel_user_ids=user_ids)
    return {
        **payload,
        "items": [_enrich_session_item(item, names_by_user_id=names) for item in items],
    }


def _enrich_session_detail(
    session: Session,
    *,
    team_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    user_id = payload.get("user_id")
    names = _member_display_names(
        session,
        team_id=team_id,
        channel_user_ids={str(user_id)} if user_id else set(),
    )
    return _enrich_session_item(payload, names_by_user_id=names)


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


def register_assistant_sessions_routes(
    app,
    get_db,
    require_capability,
    team_repo_fn,
    config: AppConfig,
) -> None:
    @app.get("/api/v2/assistant/sessions")
    def assistant_sessions_list(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:sessions:read:self")),
        status: Annotated[str | None, Query()] = None,
        member_user_id: Annotated[str | None, Query()] = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        team, _repo = team_repo_fn(session)
        params: dict[str, Any] = {
            "team_id": team.id,
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status
        perms = resolve_permissions(user.member)
        if "assistant:sessions:read:all" not in perms:
            params["member_user_id"] = user.member.dingtalk_user_id
        elif member_user_id:
            params["member_user_id"] = member_user_id
        payload = _proxy_assistant(
            config,
            user=user,
            method="GET",
            path="/api/assistant/v1/sessions",
            params=params,
        )
        return _enrich_sessions_payload(session, team_id=team.id, payload=payload)

    @app.get("/api/v2/assistant/sessions/{session_id}")
    def assistant_session_detail(
        session_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("assistant:sessions:read:self")),
    ):
        team, _repo = team_repo_fn(session)
        payload = _proxy_assistant(
            config,
            user=user,
            method="GET",
            path=f"/api/assistant/v1/sessions/{session_id}",
        )
        return _enrich_session_detail(session, team_id=team.id, payload=payload)

    @app.post("/api/v2/assistant/sessions/{session_id}/close")
    def assistant_session_close(
        session_id: str,
        user: PortalUser = Depends(require_capability("assistant:sessions:read:self")),
        reason: str = "manual",
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="POST",
            path=f"/api/assistant/v1/sessions/{session_id}/close",
            json_body={"reason": reason},
        )

    @app.get("/api/v2/assistant/sessions/{session_id}/export")
    def assistant_session_export(
        session_id: str,
        user: PortalUser = Depends(require_capability("assistant:sessions:export:self")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="GET",
            path=f"/api/assistant/v1/sessions/{session_id}/export",
        )

    @app.delete("/api/v2/assistant/sessions/{session_id}")
    def assistant_session_delete(
        session_id: str,
        user: PortalUser = Depends(require_capability("assistant:sessions:delete:self")),
    ):
        return _proxy_assistant(
            config,
            user=user,
            method="DELETE",
            path=f"/api/assistant/v1/sessions/{session_id}",
        )

    @app.get("/api/v2/assistant/profiles/me")
    def assistant_profiles_me(
        user: PortalUser = Depends(require_capability("assistant:sessions:read:self")),
        team_id: Annotated[str | None, Query()] = None,
    ):
        session = next(get_db())
        try:
            team, _repo = team_repo_fn(session)
            effective_team_id = team_id or team.id
            return _proxy_assistant(
                config,
                user=user,
                method="GET",
                path="/api/assistant/v1/profiles/me",
                params={
                    "user_id": user.member.dingtalk_user_id,
                    "team_id": effective_team_id,
                },
            )
        finally:
            session.close()

    @app.post("/api/v2/assistant/profiles/corrections")
    def assistant_profiles_correction(
        body: dict[str, Any],
        user: PortalUser = Depends(require_capability("assistant:sessions:read:self")),
    ):
        session = next(get_db())
        try:
            team, _repo = team_repo_fn(session)
            payload = {
                "user_id": user.member.dingtalk_user_id,
                "team_id": body.get("team_id") or team.id,
                "signal_id": body.get("signal_id"),
                "correction_text": body.get("correction_text", ""),
            }
            return _proxy_assistant(
                config,
                user=user,
                method="POST",
                path="/api/assistant/v1/profiles/corrections",
                json_body=payload,
            )
        finally:
            session.close()
