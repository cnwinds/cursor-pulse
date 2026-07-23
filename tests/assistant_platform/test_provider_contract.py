from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult


def test_invoke_request_fields():
    req = CapabilityInvokeRequest(
        invocation_id="i1",
        idempotency_key="k1",
        team_id="t",
        actor_member_id="m",
        capability_key="quota.self.read",
        capability_version="1",
    )
    assert req.capability_key == "quota.self.read"


def test_result_unknown_is_valid_status():
    r = CapabilityInvokeResult(status="unknown", user_message="状态待确认")
    assert r.status == "unknown"
