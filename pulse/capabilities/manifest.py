from __future__ import annotations

from typing import Any

from assistant_platform.capabilities.catalog import CAPABILITY_OPERATIONS

_PHASE1_OPERATIONS: list[dict[str, Any]] = list(CAPABILITY_OPERATIONS)

_INDEX: dict[tuple[str, str], dict[str, Any]] = {
    (op["capability_key"], op["capability_version"]): op for op in _PHASE1_OPERATIONS
}


def list_operations() -> list[dict[str, Any]]:
    return list(_PHASE1_OPERATIONS)


def get_manifest(capability_key: str, capability_version: str) -> dict[str, Any] | None:
    return _INDEX.get((capability_key, capability_version))
