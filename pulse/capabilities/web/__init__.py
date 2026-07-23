"""Web search / fetch capability helpers (Pulse Provider layer)."""

from pulse.capabilities.web.provider import SearchProviderError, get_search_provider
from pulse.capabilities.web.types import WebFetchResult, WebSearchHit, WebSearchResponse

__all__ = [
    "SearchProviderError",
    "WebFetchResult",
    "WebSearchHit",
    "WebSearchResponse",
    "get_search_provider",
]
