from __future__ import annotations

import json
from dataclasses import dataclass

from pulse.domain import ParseSummary, UsageEventRecord
from pulse.extract.csv_parser import build_parse_summary, record_from_mapped_row

VISION_JSON_SCHEMA = """
{
  "confidence": 0.0,
  "warnings": ["string"],
  "records": [
    {
      "date": "ISO8601",
      "kind": "Included|Errored|...",
      "model": "auto",
      "max_mode": false,
      "input_with_cache_write": 0,
      "input_without_cache_write": 0,
      "cache_read": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "cost": "Included|Free|0.05|-",
      "cloud_agent_id": "",
      "automation_id": ""
    }
  ]
}
"""


@dataclass(frozen=True)
class VisionExtractResult:
    records: list[UsageEventRecord]
    summary: ParseSummary
    confidence: float
    warnings: list[str]


def _vision_row_to_mapped(row: dict) -> dict[str, str]:
    return {
        "Date": str(row.get("date") or row.get("Date") or ""),
        "Cloud Agent ID": str(row.get("cloud_agent_id") or row.get("Cloud Agent ID") or ""),
        "Automation ID": str(row.get("automation_id") or row.get("Automation ID") or ""),
        "Kind": str(row.get("kind") or row.get("Kind") or "Unknown"),
        "Model": str(row.get("model") or row.get("Model") or "unknown"),
        "Max Mode": "Yes" if row.get("max_mode") in (True, "yes", "Yes", "true", "1") else "No",
        "Input (w/ Cache Write)": str(row.get("input_with_cache_write", row.get("tokens_input_cache_write", 0))),
        "Input (w/o Cache Write)": str(row.get("input_without_cache_write", row.get("tokens_input_no_cache", 0))),
        "Cache Read": str(row.get("cache_read", row.get("tokens_cache_read", 0))),
        "Output Tokens": str(row.get("output_tokens", row.get("tokens_output", 0))),
        "Total Tokens": str(row.get("total_tokens", row.get("tokens_total", 0))),
        "Cost": str(row.get("cost") or row.get("Cost") or "-"),
    }


def parse_vision_response(raw: str) -> VisionExtractResult:
    """将 Vision 模型 JSON 响应解析为结构化记录。"""
    payload = json.loads(raw)
    confidence = float(payload.get("confidence", 0))
    warnings = [str(w) for w in (payload.get("warnings") or [])]
    rows = payload.get("records") or []
    if not rows:
        raise ValueError("Vision 响应未包含任何 records")

    records: list[UsageEventRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mapped = _vision_row_to_mapped(row)
        if not mapped["Date"]:
            warnings.append("跳过缺少日期的行")
            continue
        records.append(record_from_mapped_row(mapped))

    if not records:
        raise ValueError("Vision 响应无有效记录行")

    summary = build_parse_summary(records)
    return VisionExtractResult(
        records=records,
        summary=summary,
        confidence=max(0.0, min(1.0, confidence)),
        warnings=warnings,
    )
