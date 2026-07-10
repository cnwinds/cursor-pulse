"""ASGI entry for uvicorn --reload (must be an import string target)."""

from __future__ import annotations

import os

from pulse.config import load_config
from pulse.storage.db import init_db
from pulse.web.app import create_app

_config_path = os.environ.get("PULSE_CONFIG", "config.yaml")
_config = load_config(_config_path)
_session_factory = init_db(_config.storage.database_url)
app = create_app(_config, _session_factory)
