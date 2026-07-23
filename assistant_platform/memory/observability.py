"""Structured observability for chat memory and web search.

Logs contain counts, statuses, and identifiers only — never message body text,
search queries, API keys, or other secrets.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_archive_stage(
    *,
    session_id: str,
    team_id: str,
    stage: str,
    status: str,
    duration_ms: int | None = None,
    attempt_count: int | None = None,
    chunk_count: int | None = None,
    index_version: int | None = None,
    error_code: str | None = None,
) -> None:
    parts = [
        f"event=archive_stage",
        f"session_id={session_id}",
        f"team_id={team_id}",
        f"stage={stage}",
        f"status={status}",
    ]
    if duration_ms is not None:
        parts.append(f"duration_ms={duration_ms}")
    if attempt_count is not None:
        parts.append(f"attempt_count={attempt_count}")
    if chunk_count is not None:
        parts.append(f"chunk_count={chunk_count}")
    if index_version is not None:
        parts.append(f"index_version={index_version}")
    if error_code:
        parts.append(f"error_code={error_code[:80]}")
    logger.info(" ".join(parts))


def log_recall_bundle(
    *,
    team_id: str,
    subject_id: str,
    scope: str,
    sources: tuple[str, ...] | list[str],
    fragment_count: int,
    fact_count: int,
    profile_count: int,
    token_estimate: int,
    total_hits: int,
    degraded: bool = False,
    degrade_reason: str | None = None,
) -> None:
    parts = [
        "event=recall_bundle",
        f"team_id={team_id}",
        f"subject_id={subject_id}",
        f"scope={scope}",
        f"sources={','.join(sources) or 'none'}",
        f"fragment_count={fragment_count}",
        f"fact_count={fact_count}",
        f"profile_count={profile_count}",
        f"token_estimate={token_estimate}",
        f"total_hits={total_hits}",
        f"degraded={str(degraded).lower()}",
    ]
    if degrade_reason:
        parts.append(f"degrade_reason={degrade_reason[:80]}")
    logger.info(" ".join(parts))


def log_memory_tool(
    *,
    tool: str,
    team_id: str,
    subject_id: str,
    ok: bool,
    hit_count: int | None = None,
    prev_count: int | None = None,
    next_count: int | None = None,
    session_id: str | None = None,
) -> None:
    parts = [
        f"event=memory_tool",
        f"tool={tool}",
        f"team_id={team_id}",
        f"subject_id={subject_id}",
        f"ok={str(ok).lower()}",
    ]
    if session_id:
        parts.append(f"session_id={session_id}")
    if hit_count is not None:
        parts.append(f"hit_count={hit_count}")
    if prev_count is not None:
        parts.append(f"prev_count={prev_count}")
    if next_count is not None:
        parts.append(f"next_count={next_count}")
    logger.info(" ".join(parts))


def log_web_search(
    *,
    status: str,
    provider: str | None = None,
    result_count: int | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> None:
    parts = [
        "event=web_search",
        f"status={status}",
    ]
    if provider:
        parts.append(f"provider={provider}")
    if result_count is not None:
        parts.append(f"result_count={result_count}")
    if error_code:
        parts.append(f"error_code={error_code}")
    if retryable is not None:
        parts.append(f"retryable={str(retryable).lower()}")
    logger.info(" ".join(parts))


def safe_error_code(exc: BaseException) -> str:
    """Return a short error class name for logs (no message body)."""
    return type(exc).__name__
