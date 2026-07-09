import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pulse.integrations.cursor_api import CursorApiClient, map_usage_event

FIXTURES = Path(__file__).parent / "fixtures"


def test_map_usage_event():
    raw = json.loads((FIXTURES / "cursor_usage_events.json").read_text())[
        "usageEventsDisplay"
    ][0]
    dto = map_usage_event(raw)
    assert dto.model == "composer-2.5"
    assert dto.cost_usd == pytest.approx(0.0345)
    assert dto.external_id


@patch("httpx.Client")
def test_exchange_api_key(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"accessToken": "tok", "refreshToken": "ref"}
    mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

    client = CursorApiClient()
    token = client.exchange_api_key("crsr_test")
    assert token == "tok"
