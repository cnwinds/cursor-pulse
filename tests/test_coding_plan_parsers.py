from __future__ import annotations

import json
from pathlib import Path

from pulse.integrations.coding_plan.minimax import parse_minimax_tiers
from pulse.integrations.coding_plan.zhipu import parse_zhipu_token_tiers

FIXTURES = Path(__file__).parent / "fixtures" / "coding_plan"


def test_zhipu_unit_classifies_five_hour_and_weekly():
    data = json.loads((FIXTURES / "zhipu_two_tiers.json").read_text())["data"]
    tiers = parse_zhipu_token_tiers(data)
    assert len(tiers) == 2
    assert tiers[0].name == "five_hour"
    assert tiers[0].utilization_pct == 1.0
    assert tiers[1].name == "weekly_limit"
    assert tiers[1].utilization_pct == 42.0


def test_minimax_general_two_tiers():
    body = json.loads((FIXTURES / "minimax_general.json").read_text())
    tiers = parse_minimax_tiers(body)
    assert len(tiers) == 2
    assert tiers[0].name == "five_hour"
    assert tiers[0].utilization_pct == 2.0
    assert tiers[1].name == "weekly_limit"
    assert tiers[1].utilization_pct == 5.0
