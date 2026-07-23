from __future__ import annotations

import json
import re

_NUMBER_RE = re.compile(
    r"(?<![\w.])(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)(?:%|(?=[\s，。,.、)\]]|$))"
)


def _normalize_number(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def numbers_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(1)
        found.add(_normalize_number(raw))
    return found


def allowed_numbers_from_metrics(metrics: dict) -> set[str]:
    blob = json.dumps(metrics, ensure_ascii=False)
    allowed = numbers_in_text(blob)
    expanded: set[str] = set()
    for value in allowed:
        expanded.add(value)
        if "." in value:
            expanded.add(value.rstrip("0").rstrip("."))
        try:
            as_float = float(value)
            if as_float == int(as_float):
                expanded.add(str(int(as_float)))
        except ValueError:
            pass
    return expanded


def find_unauthorized_numbers(narrative: str, metrics: dict) -> list[str]:
    """返回叙述中出现、但不在 metrics JSON 中的数字 token。"""
    allowed = allowed_numbers_from_metrics(metrics)
    violations: list[str] = []
    for number in sorted(numbers_in_text(narrative)):
        if number not in allowed:
            violations.append(number)
    return violations
