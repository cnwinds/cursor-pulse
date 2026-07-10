from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.config import AppConfig
from pulse.llm.client import build_llm_client
from pulse.storage.models import KnowledgeEntry, Member

logger = logging.getLogger(__name__)

_TIP_PREFIXES = ("心得", "技巧", "tip:", "tips:", "分享", "本月技巧", "本月心得")


def looks_like_tip(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 8:
        return False
    lower = t.lower()
    if lower.startswith(_TIP_PREFIXES):
        return True
    if t.startswith("【心得】") or t.startswith("【技巧】"):
        return True
    return False


def extract_tip_body(text: str) -> str:
    t = text.strip()
    for prefix in _TIP_PREFIXES:
        if t.lower().startswith(prefix.lower()):
            return t[len(prefix) :].lstrip("：:").strip()
    for marker in ("【心得】", "【技巧】"):
        if t.startswith(marker):
            return t[len(marker) :].strip()
    return t


ORGANIZE_SYSTEM = """你是 AI 开发工具使用心得整理助手。将用户原始输入整理为知识库条目。

要求：
1. 输出必须是合法 JSON，不要 markdown 代码块。
2. 字段：title（≤40字）、body（整理后的正文，保留原意）、tags（字符串数组，2-5个）、vendor_slug（cursor/zhipu/minimax/codex 或 null）。
3. 不要编造用户未提到的具体数字或花费。
4. 语气简洁、可复用，适合团队内部分享。"""


def _organize_with_rules(raw: str) -> dict:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    title = lines[0][:40] if lines else raw[:40]
    body = raw.strip()
    tags: list[str] = []
    lower = raw.lower()
    for slug, keywords in (
        ("cursor", ("cursor", "composer", "tab")),
        ("zhipu", ("glm", "智谱")),
        ("minimax", ("minimax",)),
        ("codex", ("codex", "chatgpt")),
    ):
        if any(k in lower for k in keywords):
            tags.append(slug)
    return {
        "title": title,
        "body": body,
        "tags": tags or ["general"],
        "vendor_slug": tags[0] if tags else None,
    }


def organize_tip(raw: str, config: AppConfig | None = None) -> dict:
    client = build_llm_client(config) if config else None
    if client is None:
        return _organize_with_rules(raw)

    user = f"请整理以下心得：\n\n{raw}"
    try:
        text = client.complete(system=ORGANIZE_SYSTEM, user=user)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        return {
            "title": str(data.get("title") or raw[:40])[:256],
            "body": str(data.get("body") or raw),
            "tags": list(data.get("tags") or []),
            "vendor_slug": data.get("vendor_slug"),
        }
    except Exception:
        logger.exception("LLM tip organize failed, using rules")
        return _organize_with_rules(raw)


class KnowledgeService:
    def __init__(self, session: Session, team_id: str, config: AppConfig | None = None):
        self.session = session
        self.team_id = team_id
        self.config = config

    def create_from_raw(
        self,
        *,
        author: Member,
        raw_text: str,
        source_channel: str,
        period: str | None = None,
    ) -> KnowledgeEntry:
        body_text = extract_tip_body(raw_text)
        organized = organize_tip(body_text, self.config)
        vendor_id = self._resolve_vendor_id(organized.get("vendor_slug"))

        now = datetime.now(timezone.utc)
        entry = KnowledgeEntry(
            team_id=self.team_id,
            author_member_id=author.id,
            vendor_id=vendor_id,
            period=period,
            title=organized["title"],
            body=organized["body"],
            raw_input=raw_text,
            tags=organized.get("tags") or [],
            source_channel=source_channel,
            status="published",
            created_at=now,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def list_entries(
        self,
        *,
        period: str | None = None,
        status: str | None = "published",
        include_hidden: bool = False,
    ) -> list[KnowledgeEntry]:
        query = (
            select(KnowledgeEntry)
            .options(joinedload(KnowledgeEntry.author_member), joinedload(KnowledgeEntry.vendor))
            .where(KnowledgeEntry.team_id == self.team_id)
        )
        if period:
            query = query.where(KnowledgeEntry.period == period)
        if status and not include_hidden:
            query = query.where(KnowledgeEntry.status == status)
        elif not include_hidden:
            query = query.where(KnowledgeEntry.status != "hidden")
        return list(
            self.session.scalars(
                query.order_by(KnowledgeEntry.pinned.desc(), KnowledgeEntry.created_at.desc())
            )
        )

    def update_entry(self, entry_id: str, **fields) -> KnowledgeEntry:
        entry = self.session.get(KnowledgeEntry, entry_id)
        if not entry or entry.team_id != self.team_id:
            raise ValueError("条目不存在")
        for key, value in fields.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        self.session.flush()
        return entry

    def build_monthly_digest(self, period: str, *, limit: int = 3) -> str:
        entries = self.list_entries(period=period)
        if not entries:
            return ""

        pinned = [e for e in entries if e.pinned]
        rest = [e for e in entries if not e.pinned]
        selected = (pinned + rest)[:limit]

        lines = [
            f"💡 {period} AI 使用技巧精选",
            "",
        ]
        for idx, entry in enumerate(selected, 1):
            author = entry.author_member.display_name if entry.author_member else "同事"
            vendor = entry.vendor.name if entry.vendor else "通用"
            tags = "、".join(entry.tags or []) or "技巧"
            lines.append(f"{idx}. 【{vendor}】{entry.title}")
            lines.append(f"   — {author} · {tags}")
            snippet = entry.body.replace("\n", " ")[:120]
            if len(entry.body) > 120:
                snippet += "…"
            lines.append(f"   {snippet}")
            lines.append("")

        lines.append("欢迎继续分享：私聊发送「心得：你的技巧」或群内 @我")
        return "\n".join(lines).strip()

    def _resolve_vendor_id(self, slug: str | None) -> str | None:
        if not slug:
            return None
        from pulse.tool_center.repository import ToolCenterRepository

        vendor = ToolCenterRepository(self.session, self.team_id).get_vendor_by_slug(str(slug))
        return vendor.id if vendor else None
