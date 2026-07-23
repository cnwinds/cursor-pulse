from __future__ import annotations

from dataclasses import dataclass


_ADMIN_MARKER_KEYS = frozenset(
    {
        "usage.aggregate",
        "report.publish",
        "submission.status.read",
        "members.manage",
        "alerts.run",
        "usage.export",
        "guide_image.update",
    }
)


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    name: str
    summary: str
    when_to_use: tuple[str, ...]
    audience: frozenset[str]
    aliases: tuple[str, ...] = ()
    privacy: str | None = None
    pending_hint: bool = False


@dataclass(frozen=True)
class SkillActorContext:
    member_id: str
    role: str | None
    authorized_capability_keys: frozenset[str]

    @property
    def is_admin(self) -> bool:
        if self.role in ("owner", "operator"):
            return True
        return bool(_ADMIN_MARKER_KEYS & self.authorized_capability_keys)

    @property
    def audiences(self) -> frozenset[str]:
        tags: set[str] = {"member"}
        if self.is_admin:
            tags.add("admin")
        return frozenset(tags)


@dataclass(frozen=True)
class SkillDocResult:
    skill_id: str
    markdown: str
    truncated: bool = False
    total_lines: int = 0
    start_line: int = 1
    end_line: int = 0
    loaded_lines: int = 0
    has_more: bool = False
    next_start_line: int | None = None
