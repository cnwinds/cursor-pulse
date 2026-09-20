"""Jev composite for Key Loan Assignment Score.

TypeSafe pattern: atomic questions in one request, combine in code.
Fail-open: any error returns an empty map so the rule score stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_WASTE_WEIGHT = 0.40
_SAFE_WEIGHT = 0.35
_FIT_WEIGHT = 0.25
_FIT_LEVELS = 3.0


class JevEvaluator(Protocol):
    def system_one(
        self, *, state: Any, questions: dict[str, dict], model: str | None = None
    ) -> dict: ...


@dataclass(frozen=True)
class JevAccountScore:
    blend: float
    waste: float
    safe: float
    fit: float
    confidence: float | None


def _candidate_card(payload: dict) -> dict:
    return {
        "id": payload.get("account_id"),
        "hours_to_deadline": payload.get("hours_to_deadline"),
        "days_to_deadline": payload.get("days_to_deadline"),
        "surplus_cents": payload.get("surplus_cents"),
        "remaining_headroom_pct": payload.get("remaining_headroom_pct"),
        "total_pct": payload.get("total_pct"),
        "auto_pct": payload.get("auto_pct"),
        "api_pct": payload.get("api_pct"),
        "active_loans": payload.get("active_loans"),
        "manual_boost": payload.get("score_adjust"),
        "status": payload.get("status"),
        "primary_member": payload.get("primary_member_name"),
        "quota_pool": payload.get("quota_pool"),
    }


def _questions_for(account_id: str, quota_pool: str | None) -> dict[str, dict]:
    pool_label = quota_pool or "combined (borrower may use Auto or API)"
    return {
        f"waste:{account_id}": {
            "type": "noul",
            "instructions": {
                "candidate_id": account_id,
                "question": (
                    "If this Cursor account is not lent to another member, is leftover "
                    "included quota likely to be wasted when the billing cycle resets?"
                ),
            },
            "criteria": {
                "true": "Material leftover versus remaining time; primary burn will not consume it",
                "false": "Primary user or current load will consume it, or little remains",
            },
        },
        f"safe:{account_id}": {
            "type": "noul",
            "instructions": {
                "candidate_id": account_id,
                "question": (
                    "Is lending this account to another member unlikely to leave the "
                    "primary user short before reset?"
                ),
            },
            "criteria": {
                "true": "Projected surplus covers a borrower without squeezing the primary user",
                "false": "Tight headroom or already on track to exhaust before reset",
            },
        },
        f"fit:{account_id}": {
            "type": "score",
            "instructions": {
                "candidate_id": account_id,
                "requested_quota_pool": pool_label,
                "question": (
                    "How good is this account as the assignment target for the requested quota pool?"
                ),
            },
            "criteria": [
                "Poor: requested pool is empty or lending would harm the primary user",
                "Usable but leftover is not at risk of being wasted at reset",
                "Good leftover to digest and the primary user is still protected",
                "Excellent: high waste risk, requested pool has room, primary buffer is healthy",
            ],
        },
    }


def _noul(answer: dict | None) -> float:
    if not answer or answer.get("type") != "noul":
        return 0.0
    try:
        return max(0.0, min(1.0, float(answer.get("noul") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _score_norm(answer: dict | None) -> tuple[float, float | None]:
    if not answer or answer.get("type") != "score":
        return 0.0, None
    try:
        raw = float(answer.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0, None
    conf = answer.get("confidence")
    try:
        confidence = None if conf is None else float(conf)
    except (TypeError, ValueError):
        confidence = None
    return max(0.0, min(1.0, raw / _FIT_LEVELS)), confidence


def blend_jev_answers(answers: dict, account_id: str) -> JevAccountScore:
    waste = _noul(answers.get(f"waste:{account_id}"))
    safe = _noul(answers.get(f"safe:{account_id}"))
    fit, confidence = _score_norm(answers.get(f"fit:{account_id}"))
    blend = _WASTE_WEIGHT * waste + _SAFE_WEIGHT * safe + _FIT_WEIGHT * fit
    return JevAccountScore(
        blend=round(blend, 4),
        waste=round(waste, 4),
        safe=round(safe, 4),
        fit=round(fit, 4),
        confidence=None if confidence is None else round(confidence, 4),
    )


def score_assignment_candidates(
    client: JevEvaluator,
    payloads: list[dict],
    *,
    quota_pool: str | None = None,
    min_confidence: float = 0.35,
) -> dict[str, JevAccountScore]:
    """Return per-account Jev blends. Empty dict on failure (fail-open)."""
    if not payloads:
        return {}
    cards = [_candidate_card(row) for row in payloads if row.get("account_id")]
    if not cards:
        return {}
    questions: dict[str, dict] = {}
    for card in cards:
        questions.update(_questions_for(str(card["id"]), quota_pool))
    state = {
        "goal": (
            "Assign a Cursor account to a borrower. Prefer leftover included quota "
            "that would be wasted at reset. Do not squeeze the primary user. "
            "Match the requested quota pool when one is specified."
        ),
        "requested_quota_pool": quota_pool or "combined",
        "candidates": cards,
    }
    try:
        result = client.system_one(state=state, questions=questions)
    except Exception:
        logger.warning("Jev assignment scoring failed; using rule scores", exc_info=True)
        return {}
    answers = result.get("answers") or {}
    if not isinstance(answers, dict):
        return {}
    out: dict[str, JevAccountScore] = {}
    for card in cards:
        account_id = str(card["id"])
        scored = blend_jev_answers(answers, account_id)
        if scored.confidence is not None and scored.confidence < min_confidence:
            continue
        out[account_id] = scored
    return out
