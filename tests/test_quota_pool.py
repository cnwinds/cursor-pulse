from __future__ import annotations

import pytest
from pulse.tool_center.quota_pool import quota_pool_for_model


@pytest.mark.parametrize(
    ("model", "want"),
    [
        # 与 proxy/billing_pool_test.go TestQuotaPoolForModel 同表
        ("composer-2.5", "auto"),
        ("cursor-grok-4.5-high", "auto"),
        ("auto", "auto"),
        ("default", "auto"),
        ("claude-4-sonnet", "api"),
        ("gpt-5.6-sol-medium", "api"),
        ("glm-4", "api"),
        ("glm-5.2-high", "api"),
        # BYOK / 自建第三方按 auto
        ("GLM-5.2", "auto"),
        ("MiniMax-M2.7", "auto"),
        # 仅借用发放无目标模型时走到这里；Go 无模型按 auto
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_quota_pool_for_model(model, want):
    assert quota_pool_for_model(model) == want
