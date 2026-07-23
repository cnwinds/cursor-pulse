"""Safe HTTP(S) fetch with SSRF checks on each hop."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from pulse.capabilities.web.provider import SearchProviderError
from pulse.capabilities.web.ssrf import (
    SsrfBlockedError,
    build_pinned_request,
    join_redirect,
    resolve_and_validate_url,
)
from pulse.capabilities.web.types import WebFetchResult

_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _content_type_allowed(content_type: str) -> bool:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if not base:
        return False
    return any(base == allowed or base.startswith(allowed) for allowed in _ALLOWED_CONTENT_TYPES)


def _extract_text(body: bytes, content_type: str, *, max_chars: int = 50_000) -> tuple[str, str, bool]:
    text = body.decode("utf-8", errors="replace")
    title = ""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if "html" in base:
        match = _TITLE_RE.search(text)
        if match:
            title = _WS_RE.sub(" ", match.group(1)).strip()
        text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return title, text, truncated


def _parse_content_length(headers: httpx.Headers) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_body_with_limit(response: httpx.Response, *, max_bytes: int) -> bytes:
    declared = _parse_content_length(response.headers)
    if declared is not None and declared > max_bytes:
        raise SearchProviderError(
            "response_too_large",
            f"响应超过大小限制（{max_bytes} 字节）",
        )

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise SearchProviderError(
                "response_too_large",
                f"响应超过大小限制（{max_bytes} 字节）",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_fetch(
    url: str,
    *,
    config: Any,
    client: httpx.Client | None = None,
    resolver=None,
) -> WebFetchResult:
    web_cfg = getattr(config, "web_search", None)
    timeout = float(getattr(web_cfg, "timeout_seconds", 10.0) or 10.0) if web_cfg else 10.0
    max_bytes = int(getattr(web_cfg, "fetch_max_bytes", 1_048_576) or 1_048_576) if web_cfg else 1_048_576
    max_redirects = int(getattr(web_cfg, "fetch_max_redirects", 5) or 5) if web_cfg else 5

    current = (url or "").strip()
    if not current:
        raise SearchProviderError("invalid_arguments", "缺少 url")

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        for _ in range(max_redirects + 1):
            try:
                target = resolve_and_validate_url(current, resolver=resolver)
                pinned_url, pin_headers, pin_extensions = build_pinned_request(current, target)
            except SsrfBlockedError as exc:
                raise SearchProviderError("ssrf_blocked", str(exc)) from exc

            request_headers = {
                "User-Agent": "cursor-pulse-web-fetch/1.0",
                **pin_headers,
            }
            try:
                with http.stream(
                    "GET",
                    pinned_url,
                    headers=request_headers,
                    extensions=pin_extensions,
                ) as response:
                    if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                        try:
                            current = join_redirect(current, response.headers.get("location"))
                            resolve_and_validate_url(current, resolver=resolver)
                        except SsrfBlockedError as exc:
                            raise SearchProviderError("ssrf_blocked", str(exc)) from exc
                        continue

                    if response.status_code >= 400:
                        raise SearchProviderError(
                            "provider_error",
                            f"网页返回错误（HTTP {response.status_code}）",
                            retryable=response.status_code >= 500,
                        )

                    content_type = response.headers.get("content-type", "")
                    if not _content_type_allowed(content_type):
                        raise SearchProviderError(
                            "content_type_rejected",
                            f"不支持的内容类型：{(content_type or 'unknown').split(';')[0].strip() or 'unknown'}",
                        )

                    body = _read_body_with_limit(response, max_bytes=max_bytes)
                    title, text, truncated = _extract_text(body, content_type)
                    retrieved_at = _utcnow_iso()
                    return WebFetchResult(
                        url=url.strip(),
                        final_url=str(response.url),
                        title=title,
                        content_type=content_type.split(";", 1)[0].strip().lower(),
                        text=text,
                        retrieved_at=retrieved_at,
                        truncated=bool(truncated),
                        byte_length=len(body),
                    )
            except httpx.TimeoutException as exc:
                raise SearchProviderError(
                    "provider_timeout",
                    "网页抓取超时",
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise SearchProviderError(
                    "provider_error",
                    "网页抓取失败",
                    retryable=True,
                ) from exc

        raise SearchProviderError("ssrf_blocked", "重定向次数过多")
    finally:
        if owns_client:
            http.close()
