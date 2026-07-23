from __future__ import annotations

import pytest
from pydantic import ValidationError

from pulse.config import AppConfig


def test_loan_selection_defaults():
    cfg = AppConfig()
    sel = cfg.tool_center.loan_selection
    assert sel.max_active_loans_per_account == 2
    assert sel.min_coverage_hours == 1.0
    assert sel.freshness_full_penalty_hours == 24.0
    assert sel.weight_urgency == 0.50
    assert sel.weight_surplus == 0.25
    assert sel.weight_load == 0.15
    assert sel.weight_freshness == 0.10


def test_loan_selection_yaml_override():
    cfg = AppConfig.model_validate(
        {
            "tool_center": {
                "loan_selection": {
                    "max_active_loans_per_account": 3,
                    "weight_urgency": 0.7,
                }
            }
        }
    )
    sel = cfg.tool_center.loan_selection
    assert sel.max_active_loans_per_account == 3
    assert sel.weight_urgency == 0.7
    # 未覆盖的键保持默认
    assert sel.weight_surplus == 0.25


def test_loan_selection_rejects_invalid_values():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"tool_center": {"loan_selection": {"max_active_loans_per_account": 0}}}
        )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"tool_center": {"loan_selection": {"weight_urgency": -0.1}}}
        )
