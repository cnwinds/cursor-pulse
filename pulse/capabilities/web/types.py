from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    domain: str
    snippet: str
    published_at: str | None
    retrieved_at: str
    rank: int
    provider: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebSearchResponse:
    query: str
    provider: str
    retrieved_at: str
    results: list[WebSearchHit] = field(default_factory=list)
    result_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
            "result_count": self.result_count,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass(frozen=True)
class WebFetchResult:
    url: str
    final_url: str
    title: str
    content_type: str
    text: str
    retrieved_at: str
    truncated: bool = False
    byte_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
