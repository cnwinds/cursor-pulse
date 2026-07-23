from pulse.integrations.webhook import build_bi_payload, sign_payload


def test_bi_payload_shape():
    payload = build_bi_payload(
        team_slug="acme",
        team_name="Acme",
        period="2026-06",
        metrics={"total_events": 10},
    )
    assert payload["team"]["slug"] == "acme"
    assert payload["period"] == "2026-06"
    assert payload["metrics"]["total_events"] == 10


def test_sign_payload_stable():
    payload = {"a": 1, "b": 2}
    assert sign_payload(payload, "secret") == sign_payload(payload, "secret")
