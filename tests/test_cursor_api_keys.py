from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.integrations.cursor_api import CursorApiClient


@pytest.fixture
def client():
    return CursorApiClient(api_base="https://api2.cursor.sh", timeout=5.0)


def test_create_user_api_key(client):
    with patch.object(client, "_post_dashboard", return_value={"apiKey": "crsr_newkey"}) as mock_post:
        result = client.create_user_api_key("token", "pulse-loan-test", api_key="crsr_primary")
    assert result["apiKey"] == "crsr_newkey"
    mock_post.assert_called_once_with(
        "token", "CreateUserApiKey", {"name": "pulse-loan-test"}, api_key="crsr_primary"
    )


def test_list_user_api_keys(client):
    with patch.object(
        client,
        "_post_dashboard",
        return_value={"apiKeys": [{"id": 1, "name": "cli", "maskedKey": "crsr_...abcd"}]},
    ):
        keys = client.list_user_api_keys("token")
    assert len(keys) == 1
    assert keys[0]["id"] == 1


def test_revoke_user_api_key(client):
    with patch.object(client, "_post_dashboard", return_value={}) as mock_post:
        client.revoke_user_api_key("token", 42, api_key="crsr_primary")
    mock_post.assert_called_once_with(
        "token", "RevokeUserApiKey", {"id": 42}, api_key="crsr_primary"
    )


def test_post_dashboard_retries_on_401(client):
    fail_response = MagicMock()
    fail_response.status_code = 401
    err = httpx.HTTPStatusError("401", request=MagicMock(), response=fail_response)

    with patch.object(client, "_do_post_dashboard", side_effect=[err, {"apiKeys": []}]) as mock_do:
        with patch.object(client, "get_access_token", return_value="fresh-token") as mock_token:
            result = client.list_user_api_keys("old-token", api_key="crsr_testkey")
    assert result == []
    mock_token.assert_called_once_with("crsr_testkey", force=True)
    assert mock_do.call_count == 2
