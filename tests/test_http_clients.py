"""Proxy policy for internal vs outbound httpx factories."""

from __future__ import annotations

import asyncio

import httpx

from pulse.http_clients import (
    internal_async_client,
    internal_client,
    outbound_async_client,
    outbound_client,
)


def test_internal_client_disables_env_proxies():
    with internal_client(timeout=1.0) as client:
        assert client._trust_env is False


def test_outbound_client_honors_env_proxies():
    with outbound_client(timeout=1.0) as client:
        assert client._trust_env is True


def test_internal_client_ignores_http_proxy_env(monkeypatch):
    """Black-hole HTTP_PROXY must not break loopback internal calls."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with internal_client(timeout=2.0, transport=transport) as client:
        resp = client.get("http://127.0.0.1:8090/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_outbound_client_enables_trust_env_with_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:7890")
    with outbound_client(timeout=1.0) as client:
        assert client._trust_env is True


def test_async_factories_match_sync_policy():
    async def _check() -> None:
        async with internal_async_client(timeout=1.0) as client:
            assert client._trust_env is False
        async with outbound_async_client(timeout=1.0) as client:
            assert client._trust_env is True

    asyncio.run(_check())
