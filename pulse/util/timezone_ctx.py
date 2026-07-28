from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

from zoneinfo import ZoneInfo

DEFAULT_DISPLAY_TIMEZONE = "Asia/Shanghai"
_CACHE_TTL_SECONDS = 60.0

_display_timezone: ContextVar[str] = ContextVar(
    "display_timezone",
    default=DEFAULT_DISPLAY_TIMEZONE,
)

_session_factory: Any | None = None
_base_config: Any | None = None
_cached_timezone: str | None = None
_cached_timezone_at: float = 0.0


def set_default_display_timezone(name: str) -> None:
    """Process-wide default (e.g. from config at app startup)."""
    _display_timezone.set(name or DEFAULT_DISPLAY_TIMEZONE)


def configure_display_timezone_resolver(
    config: Any,
    session_factory: Any,
) -> None:
    """Wire app startup session factory; avoids init_db() on every HTTP request."""
    global _session_factory, _base_config
    _session_factory = session_factory
    _base_config = config
    invalidate_display_timezone_cache()


def invalidate_display_timezone_cache() -> None:
    global _cached_timezone, _cached_timezone_at
    _cached_timezone = None
    _cached_timezone_at = 0.0


def display_timezone_name() -> str:
    return _display_timezone.get() or DEFAULT_DISPLAY_TIMEZONE


def display_zone() -> ZoneInfo:
    return ZoneInfo(display_timezone_name())


def timezone_from_config(config: Any) -> str:
    collection = getattr(config, "collection", None)
    if collection is not None:
        tz = getattr(collection, "timezone", None)
        if isinstance(tz, str) and tz.strip():
            return tz.strip()
    if isinstance(config, dict):
        tz = (config.get("collection") or {}).get("timezone")
        if isinstance(tz, str) and tz.strip():
            return tz.strip()
    return DEFAULT_DISPLAY_TIMEZONE


def resolve_display_timezone_name() -> str:
    """Team-effective timezone from Pulse config + team_settings (cached)."""
    global _cached_timezone, _cached_timezone_at

    now = time.monotonic()
    if _cached_timezone and (now - _cached_timezone_at) < _CACHE_TTL_SECONDS:
        return _cached_timezone

    tz_name = _load_display_timezone_name()
    _cached_timezone = tz_name
    _cached_timezone_at = now
    return tz_name


def _load_display_timezone_name() -> str:
    if _session_factory is not None and _base_config is not None:
        session = _session_factory()
        try:
            from pulse.settings.team_store import effective_config_for_tenant

            runtime = effective_config_for_tenant(session, _base_config)
            return timezone_from_config(runtime)
        except Exception:
            return timezone_from_config(_base_config)
        finally:
            session.close()

    try:
        from pulse.config import load_config
        from pulse.team_settings_loader import read_team_setting_section

        config = load_config()
        base_tz = timezone_from_config(config)
        overrides = read_team_setting_section(
            team_slug=config.tenant.slug,
            section="collection",
        )
        override_tz = overrides.get("timezone") if overrides else None
        if isinstance(override_tz, str) and override_tz.strip():
            return override_tz.strip()
        return base_tz
    except Exception:
        return display_timezone_name()


@contextmanager
def activate_display_timezone(name: str) -> Iterator[None]:
    token: Token = _display_timezone.set(name or DEFAULT_DISPLAY_TIMEZONE)
    try:
        yield
    finally:
        _display_timezone.reset(token)


@contextmanager
def activate_display_timezone_for_config(config: Any) -> Iterator[None]:
    with activate_display_timezone(timezone_from_config(config)):
        yield
