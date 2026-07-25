"""HTTP client factories with explicit proxy policy.

Internal (Pulse ↔ Assistant, control-plane URLs): never honor HTTP(S)_PROXY.
Outbound (Cursor API, DingTalk, LLM, web search): may use system proxies.
See docs/PROXY_LAYERS.md.
"""

from __future__ import annotations

import httpx


def internal_client(**kwargs) -> httpx.Client:
    """Client for service-to-service calls; bypasses HTTP(S)_PROXY / ALL_PROXY."""
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def internal_async_client(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)


def outbound_client(**kwargs) -> httpx.Client:
    """Client for public internet calls; may use system HTTP(S)_PROXY."""
    kwargs.setdefault("trust_env", True)
    return httpx.Client(**kwargs)


def outbound_async_client(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", True)
    return httpx.AsyncClient(**kwargs)
