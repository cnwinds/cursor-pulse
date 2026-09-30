"""OpenAI-compatible model catalog for Coding Plan gateway (GET /openai/v1/models)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "proxy" / "coding_plan_openai_models.json"


@lru_cache(maxsize=1)
def openai_models_payload() -> dict[str, Any]:
    raw = _CATALOG_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if payload.get("object") != "list" or not isinstance(payload.get("data"), list):
        raise ValueError(f"invalid coding plan models catalog: {_CATALOG_PATH}")
    return payload
