from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from assistant_platform.memory.archive_indexer import estimate_tokens
from assistant_platform.skills.models import SkillActorContext, SkillCard, SkillDocResult

DEFAULT_SKILL_WINDOW_LINES = 200


@dataclass(frozen=True)
class SkillIndexSource:
    """One skill file's material for building a routing embedding."""

    skill_id: str
    rel_path: str
    content_hash: str
    audience: frozenset[str]
    embed_text: str


class SkillRegistry:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root or Path(__file__).resolve().parent
        self._docs_root = self._root / "docs"
        self._cards = self._scan_docs()

    def list_cards(self, actor: SkillActorContext) -> list[SkillCard]:
        visible: list[SkillCard] = []
        for card in self._cards.values():
            if not (card.audience & actor.audiences):
                continue
            visible.append(card)
        return sorted(visible, key=lambda item: item.skill_id)

    def list_all_cards(self) -> list[SkillCard]:
        return sorted(self._cards.values(), key=lambda card: card.skill_id)

    def index_sources(self, *, body_char_budget: int = 4000) -> list[SkillIndexSource]:
        """Material for the skill vector index: hash + embed text per file.

        ``embed_text`` concatenates card metadata (name / skill_id / summary /
        when_to_use / aliases) with the (truncated) file body, matching the
        routing design's recommended embedding text.
        """
        sources: list[SkillIndexSource] = []
        for skill_id, card in sorted(self._cards.items()):
            path = self._doc_path(skill_id)
            if not path.is_file():
                continue
            raw = path.read_bytes()
            content_hash = hashlib.sha256(raw).hexdigest()
            _meta, body = self._parse_frontmatter(path)
            embed_text = self._build_embed_text(card, body, body_char_budget)
            sources.append(
                SkillIndexSource(
                    skill_id=skill_id,
                    rel_path=f"{skill_id}.md",
                    content_hash=content_hash,
                    audience=card.audience,
                    embed_text=embed_text,
                )
            )
        return sources

    @staticmethod
    def _build_embed_text(card: SkillCard, body: str, body_char_budget: int) -> str:
        header_parts = [card.name, card.skill_id, card.summary]
        header_parts.extend(card.when_to_use)
        header_parts.extend(card.aliases)
        header = " / ".join(part for part in header_parts if part)
        trimmed_body = body.strip()
        if body_char_budget > 0 and len(trimmed_body) > body_char_budget:
            trimmed_body = trimmed_body[:body_char_budget]
        if trimmed_body:
            return f"{header}\n---\n{trimmed_body}"
        return header

    def list_doc_files(self, skill_id: str | None = None) -> list[dict[str, object]]:
        skill_ids = [skill_id] if skill_id is not None else sorted(self._cards)
        rows: list[dict[str, object]] = []
        for sid in skill_ids:
            if sid not in self._cards:
                continue
            path = self._doc_path(sid)
            if not path.is_file():
                continue
            rows.append(
                {
                    "section": path.stem,
                    "rel_path": f"assistant_platform/skills/docs/{sid}.md",
                    "exists": True,
                }
            )
        return rows

    def read_doc_file(self, skill_id: str, rel_path: str) -> str:
        path = self._doc_path(skill_id)
        prefix = "assistant_platform/skills/docs/"
        candidate_rel = rel_path.removeprefix(prefix) if rel_path.startswith(prefix) else rel_path
        resolved = (self._docs_root / candidate_rel).resolve()
        if resolved != path.resolve() or path.suffix != ".md" or not path.is_file():
            raise ValueError(f"skill 文档不存在: {skill_id}/{rel_path}")
        return path.read_text(encoding="utf-8")

    def load_docs(
        self,
        skill_id: str,
        *,
        section: str = "overview,steps,examples",
        actor: SkillActorContext,
        token_budget: int = 4000,
        start_line: int = 1,
        max_lines: int = DEFAULT_SKILL_WINDOW_LINES,
    ) -> SkillDocResult:
        del section  # deprecated; each skill_id is one file
        card = self._cards.get(skill_id)
        if card is None or not (card.audience & actor.audiences):
            raise ValueError(f"skill 对当前用户不可见: {skill_id}")
        path = self._doc_path(skill_id)
        if not path.is_file():
            raise ValueError(f"skill 文档不存在: {skill_id}")
        meta, body = self._parse_frontmatter(path)
        if body.startswith("\n"):
            body = body[1:]
        body_lines = body.splitlines()
        # Ignore a single trailing empty line from file ending with \n
        if body_lines and body_lines[-1] == "":
            body_lines = body_lines[:-1]
        total_lines = len(body_lines)
        start = max(1, int(start_line or 1))
        window = max(1, int(max_lines or DEFAULT_SKILL_WINDOW_LINES))
        if total_lines == 0:
            return SkillDocResult(
                skill_id=skill_id,
                markdown="",
                total_lines=0,
                start_line=1,
                end_line=0,
                loaded_lines=0,
                has_more=False,
                next_start_line=None,
            )
        if start > total_lines:
            raise ValueError(
                f"start_line={start} 超出正文行数 total_lines={total_lines}"
            )
        end = min(total_lines, start + window - 1)
        chunk = body_lines[start - 1 : end]
        body_md = "\n".join(chunk)
        if start == 1:
            markdown = self._render_doc_section(meta, body_md)
        else:
            markdown = body_md.strip()
        truncated = False
        if estimate_tokens(markdown) > token_budget:
            markdown = self._truncate_markdown(markdown, token_budget)
            truncated = True
            markdown = f"{markdown}\n\n<!-- truncated -->"
        loaded = end - start + 1
        has_more = end < total_lines
        return SkillDocResult(
            skill_id=skill_id,
            markdown=markdown,
            truncated=truncated,
            total_lines=total_lines,
            start_line=start,
            end_line=end,
            loaded_lines=loaded,
            has_more=has_more,
            next_start_line=(end + 1) if has_more else None,
        )

    def _doc_path(self, skill_id: str) -> Path:
        return self._docs_root / f"{skill_id}.md"

    def _scan_docs(self) -> dict[str, SkillCard]:
        cards: dict[str, SkillCard] = {}
        for path in sorted(self._docs_root.rglob("*.md")):
            rel = path.relative_to(self._docs_root).as_posix()
            path_id = rel.removesuffix(".md")
            meta, body = self._parse_frontmatter(path)
            override = str(meta.get("skill_id") or "").strip()
            if override and override != path_id:
                raise ValueError(
                    f"skill_id frontmatter 与路径不一致: {rel} "
                    f"frontmatter={override!r} path={path_id!r}"
                )
            skill_id = path_id
            name = str(meta.get("name") or "").strip() or self._first_heading(body) or skill_id
            when_to_use = tuple(self._when_to_use_items(meta))
            summary = str(meta.get("summary") or "").strip() or (
                when_to_use[0] if when_to_use else name
            )
            privacy = meta.get("privacy")
            cards[skill_id] = SkillCard(
                skill_id=skill_id,
                name=name,
                summary=summary,
                when_to_use=when_to_use,
                audience=frozenset(str(x) for x in (meta.get("audience") or ["member"])),
                aliases=tuple(str(x).strip() for x in (meta.get("aliases") or []) if str(x).strip()),
                privacy=str(privacy).strip() if privacy else None,
                pending_hint=bool(meta.get("pending_hint", False)),
            )
        return cards

    def _parse_frontmatter(self, path: Path) -> tuple[dict[str, Any], str]:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        meta = yaml.safe_load(parts[1]) or {}
        if not isinstance(meta, dict):
            meta = {}
        return meta, parts[2]

    @staticmethod
    def _first_heading(body: str) -> str:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @staticmethod
    def _when_to_use_items(meta: dict[str, Any]) -> list[str]:
        """Frontmatter `when_to_use`（适用场景）；兼容中文键名。"""
        raw = meta.get("when_to_use")
        if raw is None:
            raw = meta.get("适用场景")
        if raw is None:
            return []
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            return []
        return [str(x).strip() for x in items if str(x).strip()]

    @classmethod
    def _render_doc_section(cls, meta: dict[str, Any], body: str) -> str:
        cleaned = body.strip()
        scenarios = cls._when_to_use_items(meta)
        if not scenarios:
            return cleaned
        lines = ["**适用场景**"]
        lines.extend(f"- {item}" for item in scenarios)
        if cleaned:
            return "\n".join(lines) + "\n\n" + cleaned
        return "\n".join(lines)

    def _truncate_markdown(self, markdown: str, token_budget: int) -> str:
        lines = markdown.splitlines()
        kept: list[str] = []
        for line in lines:
            candidate = "\n".join(kept + [line])
            if estimate_tokens(candidate) > token_budget and kept:
                break
            kept.append(line)
        return "\n".join(kept).strip()
