import pytest

from pulse.config import CursorTeamsConfig
from pulse.integrations.cursor_teams import CursorTeamsClient


def test_teams_api_disabled():
    client = CursorTeamsClient(CursorTeamsConfig(enabled=False))
    with pytest.raises(RuntimeError, match="未启用"):
        client.fetch_usage_summary("2026-06")


def test_teams_api_not_available(monkeypatch):
    class FakeResponse:
        status_code = 404

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("pulse.integrations.cursor_teams.httpx.Client", FakeClient)
    client = CursorTeamsClient(CursorTeamsConfig(enabled=True, admin_api_key="test-key"))
    data = client.fetch_usage_summary("2026-06")
    assert data["status"] == "not_available"
