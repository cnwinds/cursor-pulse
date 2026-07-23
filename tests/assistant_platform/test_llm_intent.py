# tests/assistant_platform/test_llm_intent.py
from __future__ import annotations

import json
from unittest.mock import MagicMock

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.llm_intent import (
    IntentClassification,
    capability_needs_extraction,
    classify_intent,
    extract_arguments,
    normalize_classification,
)


def _caps():
    return [
        ResolvedCapability(
            key="key.loan.self.read",
            version="1",
            risk_level="read",
            display_name="查看借用",
            description="查看当前借入的 Key",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            confirmation_required=False,
        ),
        ResolvedCapability(
            key="cursor.key.bind",
            version="1",
            risk_level="sensitive",
            display_name="绑定 Key",
            description="绑定 Cursor API Key",
            input_schema={
                "type": "object",
                "required": ["api_key"],
                "properties": {
                    "api_key": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            confirmation_required=True,
        ),
    ]


def test_capability_needs_extraction_when_required_fields_beyond_text():
    cap = _caps()[1]
    assert capability_needs_extraction(cap) is True
    assert capability_needs_extraction(_caps()[0]) is False


def test_normalize_rejects_unknown_key():
    allowed = {c.key: c for c in _caps()}
    raw = IntentClassification(
        decision="capability",
        capability_key="usage.export",
        confidence=0.9,
        clarify_question="",
        needs_args=False,
    )
    out = normalize_classification(raw, allowed=allowed, min_confidence=0.6)
    assert out.decision == "clarify"


def test_normalize_low_confidence_becomes_clarify():
    allowed = {c.key: c for c in _caps()}
    raw = IntentClassification(
        decision="capability",
        capability_key="key.loan.self.read",
        confidence=0.3,
        clarify_question="",
        needs_args=False,
    )
    out = normalize_classification(raw, allowed=allowed, min_confidence=0.6)
    assert out.decision == "clarify"


def test_classify_intent_parses_json_from_llm():
    client = MagicMock()
    client.complete.return_value = json.dumps(
        {
            "decision": "capability",
            "capability_key": "key.loan.self.read",
            "confidence": 0.92,
            "clarify_question": "",
            "needs_args": False,
        },
        ensure_ascii=False,
    )
    result = classify_intent(
        client,
        text="查看我借用的key",
        capabilities=_caps(),
        min_confidence=0.6,
    )
    assert result.decision == "capability"
    assert result.capability_key == "key.loan.self.read"


def test_extract_arguments_returns_dict():
    client = MagicMock()
    client.complete.return_value = json.dumps({"api_key": "sk-abc", "text": "绑定 sk-abc"})
    cap = _caps()[1]
    args = extract_arguments(client, text="绑定 sk-abc", capability=cap)
    assert args["api_key"] == "sk-abc"
