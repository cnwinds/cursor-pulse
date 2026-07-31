"""Cross-seam contract: Pulse intake OR vs Go snapshotQuotaOK per pool."""

from __future__ import annotations

import json
from pathlib import Path

from pulse.tool_center.snapshot_headroom import (
    snapshot_has_any_pool_headroom,
    snapshot_quota_ok_for_pool,
)

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "quota_pool_snapshot_contract.json"
)


def test_quota_pool_snapshot_contract_matches_fixture():
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert cases, "contract fixture must not be empty"
    for case in cases:
        auto = case["auto_pct"]
        api = case["api_pct"]
        assert snapshot_has_any_pool_headroom(auto_pct=auto, api_pct=api) is case[
            "intake_ok"
        ], case["name"]
        for pool, expected in case["snapshot_ok"].items():
            assert (
                snapshot_quota_ok_for_pool(pool, auto_pct=auto, api_pct=api) is expected
            ), f"{case['name']}/{pool}"
