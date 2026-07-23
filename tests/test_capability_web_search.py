from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import httpx
import pytest

from assistant_platform.contracts.provider import CapabilityInvokeRequest
from pulse.capabilities.invoke import invoke_capability
from pulse.capabilities.manifest import get_manifest, list_operations
from pulse.capabilities.web.fetch import safe_fetch
from pulse.capabilities.web.provider import SearchProviderError, get_search_provider
from pulse.capabilities.web.tavily import TavilySearchProvider
from pulse.capabilities.web.ssrf import SsrfBlockedError, resolve_and_validate_url
from pulse.capabilities.web.rate_limit import reset_web_rate_limits
from pulse.config import WebSearchConfig, load_config
from pulse.storage.db import init_db
from pulse.storage.models import Member
from tests.conftest import make_team_repo


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def _member(session, team_id, name="Alice"):
    m = Member(
        team_id=team_id,
        dingtalk_user_id=f"u-{name}",
        display_name=name,
        status="active",
    )
    session.add(m)
    session.flush()
    return m


def _request(*, team_id: str, actor_member_id: str, capability_key: str, arguments: dict | None = None):
    return CapabilityInvokeRequest(
        invocation_id="inv-web-1",
        idempotency_key="idem-web-1",
        team_id=team_id,
        actor_member_id=actor_member_id,
        capability_key=capability_key,
        capability_version="1",
        arguments=arguments or {},
    )


def _web_config(**overrides):
    cfg = load_config("config.yaml")
    web = WebSearchConfig(
        enabled=True,
        provider="tavily",
        api_key="tvly-test-key-not-real",
        search_url="https://api.tavily.com/search",
        timeout_seconds=10.0,
        max_results=5,
        fetch_max_bytes=1024,
        fetch_max_redirects=3,
    )
    for key, value in overrides.items():
        setattr(web, key, value)
    return cfg.model_copy(update={"web_search": web})


def test_catalog_registers_web_capabilities():
    keys = {op["capability_key"] for op in list_operations()}
    assert "web.search" in keys
    assert "web.fetch" in keys
    search = get_manifest("web.search", "1")
    fetch = get_manifest("web.fetch", "1")
    assert search is not None and search["risk_level"] == "read"
    assert fetch is not None and fetch["risk_level"] == "read"


def test_self_service_includes_web_keys():
    from assistant_platform.capabilities.catalog import SELF_SERVICE_KEYS

    assert "web.search" in SELF_SERVICE_KEYS
    assert "web.fetch" in SELF_SERVICE_KEYS


def test_tavily_search_normalizes_results():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {
                "title": "Example",
                "url": "https://example.com/a",
                "content": "Hello world snippet",
                "published_date": "2026-07-01",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    provider = TavilySearchProvider(_web_config().web_search, client=mock_client)
    result = provider.search("cursor pulse", max_results=3)

    assert result.provider == "tavily"
    assert result.result_count == 1
    hit = result.results[0]
    assert hit.title == "Example"
    assert hit.url == "https://example.com/a"
    assert hit.domain == "example.com"
    assert hit.snippet == "Hello world snippet"
    assert hit.published_at == "2026-07-01"
    assert hit.rank == 1
    assert hit.retrieved_at.endswith("Z")

    posted = mock_client.post.call_args
    assert posted.args[0] == "https://api.tavily.com/search"
    body = posted.kwargs["json"]
    assert body["query"] == "cursor pulse"
    assert body["api_key"] == "tvly-test-key-not-real"
    assert body["max_results"] == 3


def test_tavily_timeout_maps_to_provider_timeout():
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("slow")
    provider = TavilySearchProvider(_web_config().web_search, client=mock_client)
    with pytest.raises(SearchProviderError) as exc:
        provider.search("x")
    assert exc.value.error_code == "provider_timeout"
    assert exc.value.retryable is True


def test_get_search_provider_requires_key():
    cfg = _web_config(api_key="", enabled=True)
    with pytest.raises(SearchProviderError) as exc:
        get_search_provider(cfg)
    assert exc.value.error_code == "missing_api_key"


def test_ssrf_blocks_private_literal_and_metadata():
    for bad in (
        "http://127.0.0.1/x",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/admin",
        "file:///etc/passwd",
        "ftp://example.com/a",
    ):
        with pytest.raises(SsrfBlockedError):
            resolve_and_validate_url(bad)


def test_ssrf_blocks_dns_to_private_ip():
    def fake_resolver(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.1.2.3", 0)),
        ]

    with pytest.raises(SsrfBlockedError) as exc:
        resolve_and_validate_url("https://evil.example/", resolver=fake_resolver)
    assert exc.value.reason == "private_ip"


def test_ssrf_allows_public_ip():
    def fake_resolver(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]

    target = resolve_and_validate_url("https://example.com/path", resolver=fake_resolver)
    assert target.hostname == "example.com"
    assert "93.184.216.34" in target.ips


def test_build_pinned_request_uses_validated_ip_and_host_header():
    def fake_resolver(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    from pulse.capabilities.web.ssrf import build_pinned_request

    target = resolve_and_validate_url("https://example.com/path?q=1", resolver=fake_resolver)
    pinned_url, headers, extensions = build_pinned_request("https://example.com/path?q=1", target)
    assert pinned_url.startswith("https://93.184.216.34/")
    assert headers["Host"] == "example.com"
    assert extensions["sni_hostname"] == "example.com"


def _stream_response(*, content: bytes, **overrides):
    response = MagicMock()
    response.is_redirect = overrides.get("is_redirect", False)
    response.status_code = overrides.get("status_code", 200)
    response.headers = overrides.get("headers", {"content-type": "text/plain"})
    response.url = overrides.get("url", "https://example.com/page")
    response.iter_bytes.return_value = [content] if content else []
    return response


def _mock_stream_client(response: MagicMock) -> MagicMock:
    mock_client = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    mock_client.stream.return_value = context
    return mock_client


def test_safe_fetch_blocks_redirect_to_metadata():
    public = _stream_response(
        content=b"",
        is_redirect=True,
        status_code=302,
        headers={"location": "http://169.254.169.254/latest/meta-data/"},
    )

    mock_client = _mock_stream_client(public)

    def public_resolver(host, port, **kwargs):
        if host in {"169.254.169.254"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    with pytest.raises(SearchProviderError) as exc:
        safe_fetch(
            "https://example.com/start",
            config=_web_config(),
            client=mock_client,
            resolver=public_resolver,
        )
    assert exc.value.error_code == "ssrf_blocked"


def test_safe_fetch_pins_ip_against_dns_rebinding():
    resolver_calls: list[str] = []

    def rebinding_resolver(host, port, **kwargs):
        resolver_calls.append(host)
        if len(resolver_calls) == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))]

    ok = _stream_response(content=b"hello world")
    mock_client = _mock_stream_client(ok)

    result = safe_fetch(
        "https://example.com/page",
        config=_web_config(),
        client=mock_client,
        resolver=rebinding_resolver,
    )
    assert result.text == "hello world"
    stream_call = mock_client.stream.call_args
    pinned_url = stream_call.args[1]
    assert "93.184.216.34" in pinned_url
    assert stream_call.kwargs["headers"]["Host"] == "example.com"
    assert len(resolver_calls) == 1


def test_safe_fetch_rejects_large_and_bad_content_type():
    def public_resolver(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    oversized = _stream_response(
        content=b"x" * 2048,
        headers={"content-type": "text/plain"},
        url="https://example.com/big",
    )
    mock_client = _mock_stream_client(oversized)
    with pytest.raises(SearchProviderError) as exc:
        safe_fetch(
            "https://example.com/big",
            config=_web_config(fetch_max_bytes=1024),
            client=mock_client,
            resolver=public_resolver,
        )
    assert exc.value.error_code == "response_too_large"

    declared = _stream_response(
        content=b"",
        headers={"content-type": "text/plain", "content-length": "2048"},
        url="https://example.com/declared",
    )
    mock_client = _mock_stream_client(declared)
    with pytest.raises(SearchProviderError) as exc_declared:
        safe_fetch(
            "https://example.com/declared",
            config=_web_config(fetch_max_bytes=1024),
            client=mock_client,
            resolver=public_resolver,
        )
    assert exc_declared.value.error_code == "response_too_large"

    binary = _stream_response(
        content=b"\x00\x01",
        headers={"content-type": "application/octet-stream"},
        url="https://example.com/bin",
    )
    mock_client = _mock_stream_client(binary)
    with pytest.raises(SearchProviderError) as exc2:
        safe_fetch(
            "https://example.com/bin",
            config=_web_config(),
            client=mock_client,
            resolver=public_resolver,
        )
    assert exc2.value.error_code == "content_type_rejected"


def test_safe_fetch_success_html():
    def public_resolver(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    ok = _stream_response(
        content=b"<html><title>Hi</title><body><p>Hello page</p></body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
        url="https://example.com/page",
    )

    mock_client = _mock_stream_client(ok)
    result = safe_fetch(
        "https://example.com/page",
        config=_web_config(),
        client=mock_client,
        resolver=public_resolver,
    )
    assert result.title == "Hi"
    assert "Hello page" in result.text
    assert result.content_type == "text/html"
    assert result.final_url == "https://example.com/page"


def test_web_search_handler_mocked(session):
    team, _ = make_team_repo(session)
    member = _member(session, team.id)
    config = _web_config()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {
                "title": "Doc",
                "url": "https://docs.example.com/x",
                "content": "snippet",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch("pulse.capabilities.web.tavily.httpx.Client") as Client:
        Client.return_value.__enter__.return_value = mock_client
        Client.return_value.__exit__.return_value = False
        result = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                arguments={"query": "latest docs"},
            ),
            config=config,
        )

    assert result.status == "succeeded"
    assert result.result["provider"] == "tavily"
    assert result.result["result_count"] == 1
    assert result.result["results"][0]["domain"] == "docs.example.com"
    assert "tvly-test-key" not in (result.user_message or "")
    assert "api_key" not in result.result


def test_web_fetch_handler_ssrf(session):
    team, _ = make_team_repo(session)
    member = _member(session, team.id)
    result = invoke_capability(
        session,
        request=_request(
            team_id=team.id,
            actor_member_id=member.id,
            capability_key="web.fetch",
            arguments={"url": "http://127.0.0.1/secret"},
        ),
        config=_web_config(),
    )
    assert result.status == "failed"
    assert result.error_code == "ssrf_blocked"


def test_web_search_rate_limit_exceeded(session):
    reset_web_rate_limits()
    team, _ = make_team_repo(session)
    member = _member(session, team.id)
    config = _web_config(rate_limit_per_minute=2)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch("pulse.capabilities.web.tavily.httpx.Client") as Client:
        Client.return_value.__enter__.return_value = mock_client
        Client.return_value.__exit__.return_value = False
        for i in range(2):
            ok = invoke_capability(
                session,
                request=_request(
                    team_id=team.id,
                    actor_member_id=member.id,
                    capability_key="web.search",
                    arguments={"query": f"q{i}"},
                ),
                config=config,
            )
            assert ok.status == "succeeded", ok.error_code

        blocked = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                arguments={"query": "q-overflow"},
            ),
            config=config,
        )

    assert blocked.status == "failed"
    assert blocked.error_code == "rate_limit_exceeded"
    assert blocked.retryable is True
    assert blocked.result["retry_after_seconds"] >= 1
    assert mock_client.post.call_count == 2


def test_web_search_rate_limit_respects_hot_reload(session):
    reset_web_rate_limits()
    team, _ = make_team_repo(session)
    member = _member(session, team.id)
    strict = _web_config(rate_limit_per_minute=1)
    relaxed = _web_config(rate_limit_per_minute=5)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch("pulse.capabilities.web.tavily.httpx.Client") as Client:
        Client.return_value.__enter__.return_value = mock_client
        Client.return_value.__exit__.return_value = False
        first = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                arguments={"query": "one"},
            ),
            config=strict,
        )
        assert first.status == "succeeded"

        blocked = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                arguments={"query": "two"},
            ),
            config=strict,
        )
        assert blocked.error_code == "rate_limit_exceeded"

        allowed = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.search",
                arguments={"query": "three"},
            ),
            config=relaxed,
        )
        assert allowed.status == "succeeded"
        assert mock_client.post.call_count == 2


def test_web_fetch_rate_limit_exceeded(session):
    reset_web_rate_limits()
    team, _ = make_team_repo(session)
    member = _member(session, team.id)
    config = _web_config(rate_limit_per_minute=1)

    ok_response = _stream_response(
        content=b"hello",
        headers={"content-type": "text/plain"},
        url="https://example.com/a",
    )
    mock_client = _mock_stream_client(ok_response)

    from pulse.capabilities.web.ssrf import ResolvedTarget

    fake_target = ResolvedTarget(
        url="https://example.com/a",
        hostname="example.com",
        port=443,
        ips=("93.184.216.34",),
    )

    with (
        patch("pulse.capabilities.web.fetch.resolve_and_validate_url", return_value=fake_target),
        patch(
            "pulse.capabilities.web.fetch.build_pinned_request",
            return_value=("https://93.184.216.34/a", {"Host": "example.com"}, {}),
        ),
        patch("pulse.capabilities.web.fetch.httpx.Client", return_value=mock_client),
    ):
        first = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.fetch",
                arguments={"url": "https://example.com/a"},
            ),
            config=config,
        )
        assert first.status == "succeeded", first.error_code

        blocked = invoke_capability(
            session,
            request=_request(
                team_id=team.id,
                actor_member_id=member.id,
                capability_key="web.fetch",
                arguments={"url": "https://example.com/b"},
            ),
            config=config,
        )

    assert blocked.status == "failed"
    assert blocked.error_code == "rate_limit_exceeded"


def test_web_search_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-from-env")
    monkeypatch.setenv("WEB_SEARCH_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_MAX_RESULTS", "7")
    cfg = load_config("config.yaml")
    assert cfg.web_search.api_key == "tvly-from-env"
    assert cfg.web_search.enabled is True
    assert cfg.web_search.max_results == 7
    # Ensure we never stringify secrets into accidental dumps in this test surface.
    assert "tvly-from-env" not in repr(WebSearchConfig())
