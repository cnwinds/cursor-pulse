from __future__ import annotations

from typing import Any, Callable

import yaml
from fastapi import Depends, HTTPException

from assistant_platform.skills.models import SkillCard
from assistant_platform.skills.registry import SkillRegistry

_REGISTRY_ERRORS = (OSError, yaml.YAMLError, ValueError, KeyError)
_DOCS_PREFIX = "assistant_platform/skills/docs/"


def _get_registry() -> SkillRegistry:
    try:
        return SkillRegistry()
    except _REGISTRY_ERRORS as exc:
        raise HTTPException(status_code=500, detail=f"无法加载 skill 目录: {exc}") from exc


def _rel_path(skill_id: str) -> str:
    return f"{_DOCS_PREFIX}{skill_id}.md"


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
