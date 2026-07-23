from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from pulse.capabilities.web.provider import SearchProviderError
from pulse.capabilities.web.types import WebSearchHit, WebSearchResponse


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host
    except Exception:
        return ""


class TavilySearchProvider:
    """First web-search provider: Tavily Search API."""

    name = "tavily"

    def __init__(self, config: Any, *, client: httpx.Client | None = None):
        self._api_key = str(getattr(config, "api_key", "") or "")
        self._search_url = str(
            getattr(config, "search_url", "") or "https://api.tavily.com/search"
        ).rstrip("/")
        self._timeout = float(getattr(config, "timeout_seconds", 10.0) or 10.0)
        self._default_max_results = int(getattr(config, "max_results", 5) or 5)
        self._client = client

    def search(self, query: str, *, max_results: int | None = None) -> WebSearchResponse:
        q = (query or "").strip()
        if not q:
            raise SearchProviderError("invalid_arguments", "搜索词不能为空")
        if not self._api_key:
            raise SearchProviderError("missing_api_key", "未配置 Tavily API Key")

        limit = max_results if max_results is not None else self._default_max_results
        limit = max(1, min(int(limit), 10))
        retrieved_at = _utcnow_iso()
        payload = {
            "api_key": self._api_key,
            "query": q,
            "max_results": limit,
            "include_answer": False,
            "search_depth": "basic",
        }

        try:
            if self._client is not None:
                response = self._client.post(self._search_url, json=payload)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(self._search_url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                "provider_timeout",
                "搜索超时，请稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            raise SearchProviderError(
                "provider_error",
                f"搜索服务返回错误（HTTP {status}）",
                retryable=status >= 500 or status == 429,
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError(
                "provider_error",
                "搜索服务不可用",
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise SearchProviderError("provider_error", "搜索结果解析失败") from exc

        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raw_results = []

        hits: list[WebSearchHit] = []
        for idx, item in enumerate(raw_results[:limit], start=1):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            published = item.get("published_date") or item.get("published_at")
            published_at = str(published).strip() if published else None
            hits.append(
                WebSearchHit(
                    title=str(item.get("title") or "").strip() or url,
                    url=url,
                    domain=_domain_of(url),
                    snippet=str(item.get("content") or item.get("snippet") or "").strip(),
                    published_at=published_at or None,
                    retrieved_at=retrieved_at,
                    rank=idx,
                    provider=self.name,
                )
            )

        return WebSearchResponse(
            query=q,
            provider=self.name,
            retrieved_at=retrieved_at,
            results=hits,
            result_count=len(hits),
        )
