from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.evolution.models import FailureClusterRow, PromptChangeProposalRow
from assistant_platform.prompts.models import PromptFragmentRow, PromptReleaseRow
from assistant_platform.prompts.loader import (
    compose_system_supplement_from_files,
    load_manifest,
    load_prompt_fragments_from_files,
)

_PROMPT_EDITING_RETIRED_DETAIL = (
    "Prompt editing retired; edit files in assistant_platform/prompts/docs"
)


class ActorContext(BaseModel):
    member_id: str = ""
    role: str = ""
    permissions: set[str] = Field(default_factory=set)


class CreateFragmentBody(BaseModel):
    key: str
    content: str
    version: str = "1"


class FragmentInput(BaseModel):
    key: str
    content: str
    version: str = "1"


class CreateReleaseBody(BaseModel):
    name: str
    fragment_ids: list[str] = Field(default_factory=list)
    fragments: list[FragmentInput] = Field(default_factory=list)


class CanaryBody(BaseModel):
    percent: int = 10


def _parse_permissions(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _actor_dependency():
    def dependency(
        x_pulse_actor_member_id: Annotated[str | None, Header(alias="X-Pulse-Actor-Member-Id")] = None,
        x_pulse_actor_role: Annotated[str | None, Header(alias="X-Pulse-Actor-Role")] = None,
        x_pulse_actor_permissions: Annotated[
            str | None, Header(alias="X-Pulse-Actor-Permissions")
        ] = None,
    ) -> ActorContext:
        return ActorContext(
            member_id=(x_pulse_actor_member_id or "").strip(),
            role=(x_pulse_actor_role or "").strip(),
            permissions=_parse_permissions(x_pulse_actor_permissions),
        )

    return dependency


def _require_read(actor: ActorContext) -> None:
    if "assistant:prompts:read" not in actor.permissions:
        raise HTTPException(status_code=403, detail="缺少 assistant:prompts:read 权限")


def _require_write(actor: ActorContext) -> None:
    if "assistant:prompts:write" not in actor.permissions:
        raise HTTPException(status_code=403, detail="缺少 assistant:prompts:write 权限")


def _require_approve(actor: ActorContext) -> None:
    if "assistant:prompts:approve" not in actor.permissions:
        raise HTTPException(status_code=403, detail="缺少 assistant:prompts:approve 权限")


def _gone() -> None:
    raise HTTPException(status_code=410, detail=_PROMPT_EDITING_RETIRED_DETAIL)


def _file_fragments_json() -> list[dict[str, str]]:
    contents = load_prompt_fragments_from_files()
    fragments: list[dict[str, str]] = []
    for item in load_manifest():
        key = str(item["key"]).strip()
        content = contents[key]
        fragments.append(
            {
                "key": key,
                "path": str(item["path"]).strip(),
                "description": str(item.get("description") or "").strip(),
                "content_preview": content.splitlines()[0] if content else "",
            }
        )
    return fragments


def _fragment_json(row: PromptFragmentRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.key,
        "content": row.content,
        "version": row.version,
        "status": row.status,
    }


def _release_json(row: PromptReleaseRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "fragment_ids": list(row.fragment_ids_json or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _cluster_json(row: FailureClusterRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tag": row.tag,
        "session_ids": list(row.session_ids_json or []),
        "size": row.size,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _proposal_json(row: PromptChangeProposalRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "cluster_id": row.cluster_id,
        "diff_text": row.diff_text,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def register_prompt_routes(
    app,
    *,
    session_factory: sessionmaker[Session],
    require_service_token: Callable[..., None],
) -> None:
    actor_dependency = _actor_dependency()

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.get(
        "/api/assistant/v1/prompts",
        dependencies=[Depends(require_service_token)],
    )
    def list_file_prompts(actor: ActorContext = Depends(actor_dependency)):
        _require_read(actor)
        return {"fragments": _file_fragments_json()}

    @app.get(
        "/api/assistant/v1/prompts/preview",
        dependencies=[Depends(require_service_token)],
    )
    def preview_file_prompts(actor: ActorContext = Depends(actor_dependency)):
        _require_read(actor)
        return {"markdown": compose_system_supplement_from_files()}

    @app.get(
        "/api/assistant/v1/prompts/fragments",
        dependencies=[Depends(require_service_token)],
    )
    def list_fragments(
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        rows = session.scalars(
            select(PromptFragmentRow).order_by(PromptFragmentRow.key.asc())
        ).all()
        return {"items": [_fragment_json(row) for row in rows]}

    @app.post(
        "/api/assistant/v1/prompts/fragments",
        dependencies=[Depends(require_service_token)],
    )
    def create_fragment():
        _gone()

    @app.get(
        "/api/assistant/v1/prompts/releases",
        dependencies=[Depends(require_service_token)],
    )
    def list_releases(
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        rows = session.scalars(
            select(PromptReleaseRow).order_by(PromptReleaseRow.created_at.desc())
        ).all()
        return {"items": [_release_json(row) for row in rows]}

    @app.post(
        "/api/assistant/v1/prompts/releases",
        dependencies=[Depends(require_service_token)],
    )
    def create_draft_release():
        _gone()

    @app.get(
        "/api/assistant/v1/prompts/releases/{release_id}",
        dependencies=[Depends(require_service_token)],
    )
    def get_release(
        release_id: str,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        release = session.get(PromptReleaseRow, release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found")

        fragments: list[dict[str, Any]] = []
        for fragment_id in release.fragment_ids_json or []:
            fragment = session.get(PromptFragmentRow, fragment_id)
            if fragment is not None:
                fragments.append(_fragment_json(fragment))

        payload = _release_json(release)
        payload["fragments"] = fragments
        return payload

    @app.get(
        "/api/assistant/v1/prompts/clusters",
        dependencies=[Depends(require_service_token)],
    )
    def list_clusters(
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        rows = session.scalars(
            select(FailureClusterRow).order_by(FailureClusterRow.size.desc())
        ).all()
        return {"items": [_cluster_json(row) for row in rows]}

    @app.get(
        "/api/assistant/v1/prompts/proposals",
        dependencies=[Depends(require_service_token)],
    )
    def list_proposals(
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_read(actor)
        rows = session.scalars(
            select(PromptChangeProposalRow).order_by(
                PromptChangeProposalRow.created_at.desc()
            )
        ).all()
        return {"items": [_proposal_json(row) for row in rows]}

    @app.post(
        "/api/assistant/v1/prompts/proposals/{proposal_id}/approve",
        dependencies=[Depends(require_service_token)],
    )
    def approve_proposal(
        proposal_id: str,
        session: Session = Depends(get_db),
        actor: ActorContext = Depends(actor_dependency),
    ):
        _require_approve(actor)
        proposal = session.get(PromptChangeProposalRow, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.status != "draft":
            raise HTTPException(status_code=409, detail="仅 draft 提案可审批")
        proposal.status = "approved"
        session.add(proposal)
        session.commit()
        return _proposal_json(proposal)

    @app.post(
        "/api/assistant/v1/prompts/releases/{release_id}/canary",
        dependencies=[Depends(require_service_token)],
    )
    def canary_release(release_id: str):
        _gone()

    @app.post(
        "/api/assistant/v1/prompts/releases/{release_id}/promote",
        dependencies=[Depends(require_service_token)],
    )
    def promote_release_endpoint(release_id: str):
        _gone()

    @app.post(
        "/api/assistant/v1/prompts/releases/{release_id}/rollback",
        dependencies=[Depends(require_service_token)],
    )
    def rollback_release_endpoint(release_id: str):
        _gone()
