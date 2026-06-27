from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from personamem.domain import MemoryAtom, Commitment, Principle, DisclosureLog
from personamem.repository import SqlAlchemyMemoryRepository
from pulse.memory_adapter.identity import team_id_to_namespace
from pulse.storage.models import Member


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _serialize_value(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def member_name_map(session: Session, team_id: str) -> dict[str, str]:
    rows = session.scalars(select(Member).where(Member.team_id == team_id)).all()
    return {m.id: m.display_name for m in rows}


def serialize_atom(atom: MemoryAtom, *, names: dict[str, str]) -> dict:
    data = _serialize_value(atom)
    data["subject_name"] = names.get(atom.subject_id, atom.subject_id)
    return data


def serialize_commitment(item: Commitment, *, names: dict[str, str]) -> dict:
    data = _serialize_value(item)
    data["counterparty_name"] = names.get(item.counterparty_id, item.counterparty_id)
    return data


def serialize_principle(item: Principle) -> dict:
    return _serialize_value(item)


def serialize_disclosure(item: DisclosureLog, *, names: dict[str, str]) -> dict:
    data = _serialize_value(item)
    if item.audience_id:
        data["audience_name"] = names.get(item.audience_id, item.audience_id)
    return data


def serialize_evolution_action(row: dict) -> dict:
    return _serialize_value(row)


class MemoryQueryService:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id
        self.namespace = team_id_to_namespace(team_id)
        self.repo = SqlAlchemyMemoryRepository(session)

    def list_atoms(self, *, subject_id: str | None = None, q: str | None = None) -> list[dict]:
        subject_ids = [subject_id] if subject_id else []
        names = member_name_map(self.session, self.team_id)
        atoms = self.repo.list_atoms(self.namespace, subject_ids, query=q)
        return [serialize_atom(a, names=names) for a in atoms]

    def list_commitments(self, *, counterparty_id: str | None = None) -> list[dict]:
        names = member_name_map(self.session, self.team_id)
        ids = [counterparty_id] if counterparty_id else None
        items = self.repo.list_commitments(self.namespace, counterparty_ids=ids)
        return [serialize_commitment(c, names=names) for c in items]

    def list_principles(self) -> list[dict]:
        return [serialize_principle(p) for p in self.repo.list_principles(self.namespace)]

    def list_disclosure(self, *, limit: int = 50) -> list[dict]:
        names = member_name_map(self.session, self.team_id)
        logs = self.repo.list_disclosure_logs(self.namespace, limit=limit)
        return [serialize_disclosure(log, names=names) for log in logs]

    def list_evolution(self, *, limit: int = 50) -> list[dict]:
        rows = self.repo.list_evolution_actions(self.namespace, limit=limit)
        return [serialize_evolution_action(r) for r in rows]

    def add_principle(self, *, rule: str, tier: str, origin: str | None = None) -> dict:
        from personamem.domain import Principle, PrincipleTier

        principle = Principle(
            id=str(uuid.uuid4()),
            namespace=self.namespace,
            tier=PrincipleTier(tier),
            rule=rule,
            status="active",
            created_at=datetime.now(timezone.utc),
            origin=origin,
        )
        saved = self.repo.add_principle(principle)
        return serialize_principle(saved)
