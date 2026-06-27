from __future__ import annotations

import uuid

from personamem.domain import Principle, PrincipleTier
from personamem.ports import Clock, MemoryRepository

DEFAULT_BOTTOM_LINE_RULES: tuple[str, ...] = (
    "个人用量明细默认不在群聊公开，除非数据本身标记为可公开",
    "不向他人透露私聊中获得的机密信息",
    "涉及同事隐私的请求，先私下确认再行动",
    "对用户的承诺一旦做出，后续所有场景保持一致",
    "拿不准是否该说时，选择少说而不是冒险披露",
)


def seed_bottom_line_principles(
    repo: MemoryRepository,
    clock: Clock,
    namespace: str,
) -> list[Principle]:
    existing = repo.list_principles(namespace)
    if any(p.tier == PrincipleTier.BOTTOM_LINE for p in existing):
        return []

    now = clock.now()
    seeded: list[Principle] = []
    for rule in DEFAULT_BOTTOM_LINE_RULES:
        principle = Principle(
            id=str(uuid.uuid4()),
            namespace=namespace,
            tier=PrincipleTier.BOTTOM_LINE,
            rule=rule,
            status="active",
            created_at=now,
            origin="system_seed",
        )
        seeded.append(repo.add_principle(principle))
    return seeded
