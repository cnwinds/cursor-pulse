from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from typing import Any

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

TIP_MARKDOWN_GUIDE = """## 技巧说明
（适用场景、解决什么问题）

## 操作步骤
1. 第一步…
2. 第二步…

## 注意事项（可选）
- …"""

TIP_EVALUATE_SYSTEM = """你是团队技巧知识库的质量审核员。判断待收录技巧是否达标。

收录标准：
1. 说清「什么技巧」：场景/目标明确，非空泛感慨
2. 说清「具体怎么做」：有可执行步骤或操作要点，他人能照着做
3. 正文为 Markdown，结构清晰（建议含技巧说明、操作步骤）
4. 对团队其他人有实际参考价值

输出合法 JSON，不要 markdown 代码块：
{"approved": true或false, "feedback": "通过时简短肯定；不通过时说明缺什么、如何补"}
"""


class TipSubmissionError(ValueError):
    """技巧未达收录标准。"""


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


def _evaluate_tip_rules(title: str, body: str) -> tuple[bool, list[str]]:
    title = (title or "").strip()
    body = (body or "").strip()
    issues: list[str] = []

    if len(title) < 4:
        issues.append("标题过短，请用一句话概括技巧主题（至少 4 字）")
    if len(title) > 40:
        issues.append("标题不能超过 40 字")
    if len(body) < 80:
        issues.append("正文太短，请补充场景说明与具体操作步骤（建议不少于 80 字）")

    has_structure = bool(
        re.search(r"^#{1,3}\s", body, re.M)
        or re.search(r"^\s*[-*]\s", body, re.M)
        or re.search(r"^\s*\d+[.)]\s", body, re.M)
    )
    if not has_structure:
        issues.append(
            "请用 Markdown 组织正文（如 ## 技巧说明、## 操作步骤，或有序/无序列表）"
        )

    actionable_keywords = (
        "步骤", "做法", "如何", "可以", "建议", "操作", "使用", "配置", "设置", "先", "然后",
    )
    if not any(kw in body for kw in actionable_keywords):
        issues.append("缺少可执行的操作要点，请写清「具体怎么做」")

    return len(issues) == 0, issues


def evaluate_tip_submission(
    title: str,
    body: str,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """评估技巧是否达到收录标准。返回 {approved, feedback}。"""
    ok, issues = _evaluate_tip_rules(title, body)
    if not ok:
        feedback = "技巧尚未达到收录标准，请补充：\n" + "\n".join(f"- {item}" for item in issues)
        feedback += f"\n\n建议 Markdown 格式：\n{TIP_MARKDOWN_GUIDE}"
        return {"approved": False, "feedback": feedback}

    client = build_llm_client(config) if config else None
    if client is None:
        return {"approved": True, "feedback": "内容结构符合要求"}

    user = f"标题：{title.strip()}\n\n正文：\n{body.strip()}"
    try:
        text = client.complete(system=TIP_EVALUATE_SYSTEM, user=user)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        approved = bool(data.get("approved"))
        feedback = str(data.get("feedback") or "").strip()
        if not feedback:
            feedback = "内容符合收录标准" if approved else "内容不够具体，请补充场景与操作步骤"
        if not approved:
            feedback += f"\n\n建议 Markdown 格式：\n{TIP_MARKDOWN_GUIDE}"
        return {"approved": approved, "feedback": feedback}
    except Exception:
        logger.exception("LLM tip evaluate failed, using rules only")
        return {"approved": True, "feedback": "内容结构符合要求"}


def infer_tip_tags(title: str, body: str, tags: list[str] | None = None) -> list[str]:
    if tags:
        cleaned = [str(t).strip() for t in tags if str(t).strip()]
        if cleaned:
            return cleaned[:5]
    lower = f"{title}\n{body}".lower()
    found: list[str] = []
    for slug, keywords in (
        ("cursor", ("cursor", "composer", "tab", "@codebase", "@file")),
        ("zhipu", ("glm", "智谱")),
        ("minimax", ("minimax",)),
        ("codex", ("codex", "chatgpt")),
    ):
        if any(k in lower for k in keywords):
            found.append(slug)
    return found or ["general"]


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

    def create_from_submission(
        self,
        *,
        author: Member,
        title: str,
        body: str,
        source_channel: str,
        period: str | None = None,
        tags: list[str] | None = None,
        vendor_slug: str | None = None,
        raw_input: str | None = None,
        skip_evaluation: bool = False,
    ) -> KnowledgeEntry:
        title = title.strip()[:256]
        body = body.strip()
        if not skip_evaluation:
            evaluation = evaluate_tip_submission(title, body, self.config)
            if not evaluation["approved"]:
                raise TipSubmissionError(evaluation["feedback"])

        resolved_tags = infer_tip_tags(title, body, tags)
        vendor_id = self._resolve_vendor_id(vendor_slug)
        if vendor_id is None and resolved_tags and resolved_tags[0] != "general":
            vendor_id = self._resolve_vendor_id(resolved_tags[0])

        now = datetime.now(timezone.utc)
        entry = KnowledgeEntry(
            team_id=self.team_id,
            author_member_id=author.id,
            vendor_id=vendor_id,
            period=period,
            title=title,
            body=body,
            raw_input=raw_input or body,
            tags=resolved_tags,
            source_channel=source_channel,
            status="published",
            created_at=now,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def get_entry(self, entry_id: str) -> KnowledgeEntry | None:
        entry = self.session.get(KnowledgeEntry, entry_id)
        if entry is None or entry.team_id != self.team_id:
            return None
        return entry

    def find_entries_by_title(self, query: str, *, limit: int = 5) -> list[KnowledgeEntry]:
        q = (query or "").strip()
        if not q:
            return []
        pattern = f"%{q}%"
        return list(
            self.session.scalars(
                select(KnowledgeEntry)
                .options(joinedload(KnowledgeEntry.author_member), joinedload(KnowledgeEntry.vendor))
                .where(
                    KnowledgeEntry.team_id == self.team_id,
                    KnowledgeEntry.status == "published",
                    KnowledgeEntry.title.like(pattern),
                )
                .order_by(KnowledgeEntry.pinned.desc(), KnowledgeEntry.created_at.desc())
                .limit(limit)
            ).all()
        )

    def format_entry_list(self, entries: list[KnowledgeEntry]) -> str:
        if not entries:
            return "技巧库暂无已发布条目。"
        lines = [f"技巧库共 {len(entries)} 条：", ""]
        for entry in entries:
            author = entry.author_member.display_name if entry.author_member else "同事"
            period = entry.period or "—"
            lines.append(f"- [{entry.id}] {entry.title}（{period} · {author}）")
        lines.append("")
        lines.append("查看详情请提供标题或条目 ID。")
        return "\n".join(lines)

    def format_entry_detail(self, entry: KnowledgeEntry) -> str:
        author = entry.author_member.display_name if entry.author_member else "同事"
        vendor = entry.vendor.name if entry.vendor else "通用"
        tag_text = "、".join(entry.tags or []) or "技巧"
        lines = [
            f"# {entry.title}",
            "",
            f"作者：{author} · 工具：{vendor} · 标签：{tag_text}",
            f"账期：{entry.period or '—'} · ID：`{entry.id}`",
            "",
            entry.body.strip(),
        ]
        return "\n".join(lines)

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
