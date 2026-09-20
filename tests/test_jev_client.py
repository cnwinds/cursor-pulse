from __future__ import annotations

import httpx
import pytest

from pulse.config import AppConfig, JevConfig
from pulse.llm.jev import (
    JevClient,
    JevError,
    build_choice_question,
    build_jev_client,
    build_noul_question,
    build_score_question,
)


@pytest.fixture
def patch_outbound(monkeypatch):
    """把 jev 模块的 outbound_client 换成 MockTransport 版本。"""
    import pulse.llm.jev as jev_mod

    def install(handler):
        def factory(**kwargs):
            kwargs.pop("trust_env", None)
            return httpx.Client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(jev_mod, "outbound_client", factory)

    return install


def test_decide_posts_to_decisions_endpoint(patch_outbound):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "pick": {
                        "choice": "acc-2",
                        "probabilities": {"acc-1": 0.2, "acc-2": 0.8},
                        "confidence": 0.8,
                    }
                },
                "model": "typesafe/jev-1.13-20260917",
                "provider": "TypeSafe",
                "usage": {"input_tokens": 900, "output_tokens": 0, "cost": 0.0001},
            },
        )

    patch_outbound(handler)
    client = JevClient(api_key="or-key")
    decision = client.decide(
        state={"candidates": [{"account_id": "acc-1"}]},
        questions={"pick": build_choice_question("pick", {"acc-1": "idle"})},
    )

    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["auth"] == "Bearer or-key"
    assert seen["body"]["model"] == "typesafe/jev-1.13"
    # state 按参考文档序列化为字符串
    assert isinstance(seen["body"]["state"], str)
    assert "acc-1" in seen["body"]["state"]
    assert decision.model == "typesafe/jev-1.13-20260917"
    assert decision.answer("pick").choice == "acc-2"
    assert decision.answer("pick").confidence == 0.8
    assert decision.answer("pick").probabilities == {"acc-1": 0.2, "acc-2": 0.8}
    assert decision.usage["input_tokens"] == 900


def test_decide_custom_base_url_and_model(patch_outbound):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answers": {"pick": {"choice": "a"}}})

    patch_outbound(handler)
    client = JevClient(
        api_key="k", base_url="https://openrouter.ai/api/", model="typesafe/jev-latest"
    )
    client.decide(state="raw", questions={"pick": build_choice_question("x", {"a": "b"})})

    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["body"]["model"] == "typesafe/jev-latest"
    assert seen["body"]["state"] == "raw"


def test_decide_requires_questions():
    client = JevClient(api_key="k")
    with pytest.raises(JevError):
        client.decide(state={}, questions={})


def test_decide_raises_on_http_error(patch_outbound):
    patch_outbound(
        lambda request: httpx.Response(
            401, json={"error": {"code": 401, "message": "Missing Authentication header"}}
        )
    )
    client = JevClient(api_key="bad")
    with pytest.raises(JevError) as exc:
        client.decide(state={}, questions={"pick": build_choice_question("x", {"a": "b"})})
    assert "401" in str(exc.value)
    assert "Missing Authentication header" in str(exc.value)


def test_decide_raises_on_error_body_with_200(patch_outbound):
    patch_outbound(
        lambda request: httpx.Response(
            200, json={"error": {"code": 402, "message": "Insufficient credits"}}
        )
    )
    client = JevClient(api_key="k")
    with pytest.raises(JevError) as exc:
        client.decide(state={}, questions={"pick": build_choice_question("x", {"a": "b"})})
    assert "Insufficient credits" in str(exc.value)


def test_decide_raises_on_malformed_body(patch_outbound):
    patch_outbound(lambda request: httpx.Response(200, json={"model": "m"}))
    client = JevClient(api_key="k")
    with pytest.raises(JevError):
        client.decide(state={}, questions={"pick": build_choice_question("x", {"a": "b"})})


def test_decide_raises_on_network_error(patch_outbound):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    patch_outbound(handler)
    client = JevClient(api_key="k", timeout_seconds=0.01)
    with pytest.raises(JevError) as exc:
        client.decide(state={}, questions={"pick": build_choice_question("x", {"a": "b"})})
    assert "jev request failed" in str(exc.value)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (0.9, 0.9),
        (True, 1.0),
        (False, 0.0),
        ({"probability": 0.25}, 0.25),
        ({"probabilities": {"true": 0.7, "false": 0.3}}, 0.7),
        ({"choice": "a"}, None),
        (None, None),
    ],
)
def test_noul_probability_shapes(raw, want):
    from pulse.llm.jev import JevAnswer

    assert JevAnswer(name="q", raw=raw).probability == want


def test_question_builders_shape():
    assert build_choice_question("pick", {"a": "idle"}) == {
        "type": "choice",
        "instructions": "pick",
        "criteria": {"a": "idle"},
    }
    noul = build_noul_question("harm?", yes="y", no="n")
    assert noul["type"] == "noul"
    # OpenRouter 要求 noul criteria 两侧都给
    assert set(noul["criteria"]) == {"true", "false"}
    score = build_score_question("urgency", ["low", "high"])
    assert score == {"type": "score", "instructions": "urgency", "criteria": ["low", "high"]}


def test_build_jev_client_requires_enabled_and_key():
    assert build_jev_client(AppConfig()) is None
    assert build_jev_client(AppConfig(jev=JevConfig(enabled=True))) is None
    assert build_jev_client(AppConfig(jev=JevConfig(api_key="k"))) is None
    client = build_jev_client(AppConfig(jev=JevConfig(enabled=True, api_key="k")))
    assert isinstance(client, JevClient)
    assert client.model == "typesafe/jev-1.13"
    assert client.decisions_url == "https://openrouter.ai/api/alpha/decisions"
