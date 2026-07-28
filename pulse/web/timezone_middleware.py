from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from pulse.config import AppConfig
from pulse.util.timezone_ctx import (
    activate_display_timezone,
    configure_display_timezone_resolver,
    resolve_display_timezone_name,
    set_default_display_timezone,
    timezone_from_config,
)


class DisplayTimezoneMiddleware(BaseHTTPMiddleware):
    """Resolve team-effective timezone for each HTTP request."""

    def __init__(
        self,
        app,
        *,
        config: AppConfig | None = None,
        session_factory=None,
    ):
        super().__init__(app)
        if config is not None:
            set_default_display_timezone(timezone_from_config(config))
        if config is not None and session_factory is not None:
            configure_display_timezone_resolver(config, session_factory)

    async def dispatch(self, request: Request, call_next) -> Response:
        tz_name = resolve_display_timezone_name()
        with activate_display_timezone(tz_name):
            return await call_next(request)
