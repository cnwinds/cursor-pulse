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
        # BYOK / 自建第三方不占 included 桶
        ("GLM-5.2", "unknown"),
        ("MiniMax-M2.7", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_quota_pool_for_model(model, want):
    assert quota_pool_for_model(model) == want
