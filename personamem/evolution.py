from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from personamem.domain import (
    EvolutionActionProposal,
    EvolutionActionResult,
    EvolutionResult,
    Principle,
    PrincipleTier,
)


@dataclass
class LearnedPrincipleProposal:
    rule: str
    origin: str
    confidence: float = 0.8


@dataclass
class ReflectionResult:
    principles: list[LearnedPrincipleProposal]
    actions: list[EvolutionActionProposal]


class Reflector(Protocol):
    def reflect(
        self,
        *,
        namespace: str,
        recent_atom_summaries: list[str],
        disclosure_summaries: list[str],
        existing_principles: list[Principle],
    ) -> ReflectionResult: ...


class ActionExecutor(Protocol):
    def execute(
        self,
        *,
        namespace: str,
        action: EvolutionActionProposal,
    ) -> EvolutionActionResult: ...


SAFE_AUTO_ACTIONS = frozenset({
    "private_nudge_unsubmitted",
    "admin_notify",
    "group_collection_tip",
})


def _normalize_rule(text: str) -> str:
    import re

    return re.sub(r"\s+", "", text.strip().lower())


def run_evolution(
    repo: MemoryRepository,
    reflector: Reflector | None,
    clock,
    *,
    namespace: str,
    min_confidence: float = 0.7,
    executor: ActionExecutor | None = None,
    auto_execute: bool = True,
) -> EvolutionResult:
    if reflector is None:
        return EvolutionResult(principles=[], actions=[])

    recent_atoms = repo.list_atoms_since(namespace, days=7)
    logs = repo.list_disclosure_logs(namespace, limit=30)
    existing = repo.list_principles(namespace)

    atom_summaries = [a.content for a in recent_atoms[:50]]
    log_summaries = [f"{log.deflection_reason}: {log.query_excerpt}" for log in logs]

    reflection = reflector.reflect(
        namespace=namespace,
        recent_atom_summaries=atom_summaries,
        disclosure_summaries=log_summaries,
        existing_principles=existing,
    )

    existing_norm = {_normalize_rule(p.rule) for p in existing}
    added: list[Principle] = []
    now = clock.now()
    for proposal in reflection.principles:
        if proposal.confidence < min_confidence:
            continue
        norm = _normalize_rule(proposal.rule)
        if not norm or norm in existing_norm:
            continue
        principle = Principle(
            id=str(uuid.uuid4()),
            namespace=namespace,
            tier=PrincipleTier.LEARNED,
            rule=proposal.rule,
            status="active",
            created_at=now,
            origin=proposal.origin,
        )
        added.append(repo.add_principle(principle))
        existing_norm.add(norm)

    action_results: list[EvolutionActionResult] = []
    for action in reflection.actions:
        if action.confidence < min_confidence:
            action_results.append(
                EvolutionActionResult(action_type=action.action_type, status="skipped", detail="low confidence")
            )
            continue
        if auto_execute and executor and action.action_type in SAFE_AUTO_ACTIONS:
            result = executor.execute(namespace=namespace, action=action)
            repo.log_evolution_action(  # type: ignore[attr-defined]
                namespace=namespace,
                action_type=action.action_type,
                payload=action.payload,
                status=result.status,
                detail=result.detail,
            )
            action_results.append(result)
        else:
            action_results.append(
                EvolutionActionResult(action_type=action.action_type, status="skipped", detail="not auto-executed")
            )

    return EvolutionResult(principles=added, actions=action_results)
