from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pulse.capabilities.web.types import WebSearchResponse


class SearchProviderError(Exception):
    """Normalized provider failure with a stable error_code for handlers."""

    def __init__(self, error_code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int | None = None) -> WebSearchResponse:
        """Run a search and return normalized hits."""


def get_search_provider(config: Any) -> SearchProvider:
    web_cfg = getattr(config, "web_search", None)
    if web_cfg is None:
        raise SearchProviderError("web_search_disabled", "联网搜索未配置")
    if not getattr(web_cfg, "enabled", False):
        raise SearchProviderError("web_search_disabled", "联网搜索未启用")
    api_key = str(getattr(web_cfg, "api_key", "") or "").strip()
    if not api_key:
        raise SearchProviderError("missing_api_key", "未配置搜索提供商密钥")

    provider_name = str(getattr(web_cfg, "provider", "tavily") or "tavily").strip().lower()
    if provider_name == "tavily":
        from pulse.capabilities.web.tavily import TavilySearchProvider

        return TavilySearchProvider(web_cfg)
    raise SearchProviderError(
        "unsupported_provider",
        f"不支持的搜索提供商：{provider_name}",
    )
