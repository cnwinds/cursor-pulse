from assistant_platform.contracts.provider import CapabilityInvokeRequest

from pulse.capabilities.invoke import invoke_capability
from pulse.capabilities.manifest import get_manifest, list_operations


def test_manifest_contains_three_phase1_ops():
    keys = {op["capability_key"] for op in list_operations()}
    assert keys >= {"quota.self.read", "cursor.key.bind", "guide_image.update"}


def test_get_manifest_quota_self_read():
    op = get_manifest("quota.self.read", "1")
    assert op["risk_level"] == "read"
    assert op["status"] == "active"


def test_invoke_unknown_capability():
    request = CapabilityInvokeRequest(
        invocation_id="i1",
        idempotency_key="k1",
        team_id="t1",
        actor_member_id="m1",
        capability_key="nonexistent.op",
        capability_version="1",
    )
    result = invoke_capability(None, request=request, config=None)
    assert result.status == "failed"
    assert result.error_code == "unknown_capability"
