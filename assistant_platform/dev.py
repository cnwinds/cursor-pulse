"""ASGI entry for uvicorn --reload (import string target)."""

from __future__ import annotations

from assistant_platform.api.app import create_assistant_app
from assistant_platform.config import load_assistant_config
from assistant_platform.storage.db import init_assistant_db

_config = load_assistant_config()
_session_factory = init_assistant_db(_config.database_url, team_id=_config.team_id)
app = create_assistant_app(_config, _session_factory)
