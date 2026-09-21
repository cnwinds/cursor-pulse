from __future__ import annotations

from typing import Any, Callable

import yaml
from fastapi import Depends, HTTPException, Query

from assistant_platform.skills.models import SkillCard
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.util.git_file import (
    GitFileError,
    find_repo_root,
    list_file_history,
    merge_base,
    read_file_at_ref,
    read_worktree_file,
)
from assistant_platform.util.three_way_merge import build_three_way_merge_view, merge_view_to_json

_REGISTRY_ERRORS = (OSError, yaml.YAMLError, ValueError, KeyError)
_DOCS_PREFIX = "assistant_platform/skills/docs/"


def _get_registry() -> SkillRegistry:
    try:
        return SkillRegistry()
    except _REGISTRY_ERRORS as exc:
        raise HTTPException(status_code=500, detail=f"无法加载 skill 目录: {exc}") from exc


def _rel_path(skill_id: str) -> str:
    return f"{_DOCS_PREFIX}{skill_id}.md"


def _repo_for_skills() -> Path:
    registry = _get_registry()
    root = find_repo_root(registry._root)
    if root is None:
        raise HTTPException(status_code=503, detail="未找到 git 仓库，无法读取历史版本")
    return root


def _ensure_skill(skill_id: str) -> SkillCard:
    registry = _get_registry()
    card = next((item for item in registry.list_all_cards() if item.skill_id == skill_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return card


def _card_json(card: SkillCard) -> dict[str, Any]:
    return {
        "skill_id": card.skill_id,
        "name": card.name,
        "summary": card.summary,
        "when_to_use": list(card.when_to_use),
        "audience": sorted(card.audience),
        "aliases": list(card.aliases),
        "privacy": card.privacy,
        "pending_hint": card.pending_hint,
        "rel_path": _rel_path(card.skill_id),
    }


def _load_help_topics(registry: SkillRegistry) -> list[dict[str, Any]]:
    path = registry._root / "help_topics.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = raw.get("topics") or []
    if not isinstance(topics, list):
        raise ValueError("help_topics.yaml 的 topics 必须是列表")
    return [dict(item) for item in topics if isinstance(item, dict)]


def register_skills_admin_routes(
    app,
    *,
    require_service_token: Callable[..., None],
) -> None:
    @app.get(
        "/api/assistant/v1/skills",
        dependencies=[Depends(require_service_token)],
    )
    def list_skills():
        registry = _get_registry()
        return {"skills": [_card_json(card) for card in registry.list_all_cards()]}

    @app.get(
        "/api/assistant/v1/skills/help-topics",
        dependencies=[Depends(require_service_token)],
    )
    def list_help_topics():
        registry = _get_registry()
        try:
            return {"topics": _load_help_topics(registry)}
        except _REGISTRY_ERRORS as exc:
            raise HTTPException(status_code=500, detail=f"无法读取帮助主题: {exc}") from exc

    @app.get(
        "/api/assistant/v1/skills/{skill_id:path}/file-history",
        dependencies=[Depends(require_service_token)],
    )
    def skill_file_history(skill_id: str, limit: int = Query(default=40, ge=1, le=200)):
        _ensure_skill(skill_id)
        rel = _rel_path(skill_id)
        repo_root = _repo_for_skills()
        try:
            revisions = list_file_history(repo_root, rel, limit=limit)
        except GitFileError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        working_label = "工作区（当前）"
        items = [
            {
                "ref": "WORKING",
                "label": working_label,
                "committed_at": None,
                "subject": working_label,
            }
        ]
        for row in revisions:
            short = row.commit[:8]
            items.append(
                {
                    "ref": row.commit,
                    "label": f"{short} · {row.subject}",
                    "committed_at": row.committed_at,
                    "subject": row.subject,
                }
            )
        return {"rel_path": rel, "items": items}

    @app.get(
        "/api/assistant/v1/skills/{skill_id:path}/file-compare",
        dependencies=[Depends(require_service_token)],
    )
    def skill_file_compare(
        skill_id: str,
        left_ref: str = Query(..., min_length=1),
        right_ref: str = Query(..., min_length=1),
    ):
        _ensure_skill(skill_id)
        rel = _rel_path(skill_id)
        repo_root = _repo_for_skills()

        def _read_ref(ref: str) -> str:
            if ref == "WORKING":
                return read_worktree_file(repo_root, rel)
            return read_file_at_ref(repo_root, rel, ref)

        try:
            left_text = _read_ref(left_ref)
            right_text = _read_ref(right_ref)
            if left_ref == "WORKING" or right_ref == "WORKING":
                base_ref = merge_base(
                    repo_root,
                    left_ref if left_ref != "WORKING" else "HEAD",
                    right_ref if right_ref != "WORKING" else "HEAD",
                )
            else:
                base_ref = merge_base(repo_root, left_ref, right_ref)
            base_text = read_file_at_ref(repo_root, rel, base_ref)
        except GitFileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def _label(ref: str) -> str:
            if ref == "WORKING":
                return "工作区（当前）"
            return f"{ref[:8]}…"

        view = build_three_way_merge_view(
            left_text=left_text,
            base_text=base_text,
            right_text=right_text,
            left_label=_label(left_ref),
            right_label=_label(right_ref),
        )
        payload = merge_view_to_json(view)
        payload["rel_path"] = rel
        payload["base_ref"] = base_ref
        return payload

    @app.get(
        "/api/assistant/v1/skills/{skill_id:path}",
        dependencies=[Depends(require_service_token)],
    )
    def get_skill(skill_id: str):
        registry = _get_registry()
        card = next((item for item in registry.list_all_cards() if item.skill_id == skill_id), None)
        if card is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        try:
            markdown = registry.read_doc_file(skill_id, _rel_path(skill_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = _card_json(card)
        payload["markdown"] = markdown
        return payload
