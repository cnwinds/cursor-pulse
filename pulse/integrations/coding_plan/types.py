from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QuotaTier:
    name: str  # five_hour | weekly_limit
    utilization_pct: float
    resets_at: str | None = None


@dataclass
class CodingPlanExtra:
    """Serialized into AccountQuotaSnapshot.quota_extra."""

    schema_version: int
    plan_level: str | None
    tiers: list[QuotaTier]
    extras: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "plan_level": self.plan_level,
            "tiers": [
                {
                    "name": t.name,
                    "utilization_pct": t.utilization_pct,
                    "resets_at": t.resets_at,
                }
                for t in self.tiers
            ],
            "extras": self.extras,
        }

    @classmethod
    def from_json(cls, data: dict | None) -> CodingPlanExtra | None:
        if not data:
            return None
        tiers = [
            QuotaTier(
                name=str(item.get("name") or ""),
                utilization_pct=float(item.get("utilization_pct") or 0),
                resets_at=item.get("resets_at"),
            )
            for item in (data.get("tiers") or [])
        ]
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            plan_level=data.get("plan_level"),
            tiers=tiers,
            extras=list(data.get("extras") or []),
        )


@dataclass
class CodingPlanQuotaResult:
    plan_level: str | None
    tiers: list[QuotaTier]
    extras: list[dict] = field(default_factory=list)
