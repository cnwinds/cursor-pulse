"""In-process counters for capability routing (dedicated vs fallback vs missing)."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_invoke_by_capability: Counter[str] = Counter()
_invoke_by_handler_kind: Counter[str] = Counter()
_missing_handler_by_capability: Counter[str] = Counter()


def record_invoke(capability_key: str, *, handler_kind: str) -> None:
    with _lock:
        _invoke_by_capability[capability_key] += 1
        _invoke_by_handler_kind[handler_kind] += 1


def record_missing_handler(capability_key: str) -> None:
    with _lock:
        _missing_handler_by_capability[capability_key] += 1
        _invoke_by_handler_kind["missing_handler"] += 1


def snapshot() -> dict[str, object]:
    with _lock:
        return {
            "invoke_by_capability": dict(_invoke_by_capability),
            "invoke_by_handler_kind": dict(_invoke_by_handler_kind),
            "missing_handler_by_capability": dict(_missing_handler_by_capability),
            "fallback_total": _invoke_by_handler_kind.get("fallback", 0),
            "dedicated_total": _invoke_by_handler_kind.get("dedicated", 0),
        }


def reset() -> None:
    with _lock:
        _invoke_by_capability.clear()
        _invoke_by_handler_kind.clear()
        _missing_handler_by_capability.clear()
