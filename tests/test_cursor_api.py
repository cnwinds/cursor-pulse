import base64
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.integrations.cursor_api import (
    CursorApiClient,
    email_from_jwt_payload,
    map_usage_event,
    resolve_account_email_from_exchange,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_jwt(*, exp: int | None = None, email: str | None = None) -> str:
    payload: dict = {"exp": exp if exp is not None else int(time.time()) + 3600}
    if email:
        payload["email"] = email
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return f"hdr.{encoded}.sig"


def test_resolve_account_email_from_exchange():
    token = _fake_jwt(email="User@Example.com")
    assert resolve_account_email_from_exchange(
        {"accessToken": token, "refreshToken": "ref"}
    ) == "user@example.com"
    assert email_from_jwt_payload({"preferred_username": "a@b.com"}) == "a@b.com"


def _mock_exchange(mock_client_cls, token: str = "tok"):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "accessToken": token,
        "refreshToken": "ref",
    }
    mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp


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
    _mock_exchange(mock_client_cls, _fake_jwt())

    client = CursorApiClient()
    token = client.exchange_api_key("crsr_test")
    assert token.startswith("hdr.")


@patch("httpx.Client")
def test_get_access_token_uses_cache(mock_client_cls):
    jwt_token = _fake_jwt()
    _mock_exchange(mock_client_cls, jwt_token)

    client = CursorApiClient()
    assert client.get_access_token("crsr_test") == jwt_token
    assert client.get_access_token("crsr_test") == jwt_token

    post = mock_client_cls.return_value.__enter__.return_value.post
    assert post.call_count == 1


@patch("httpx.Client")
def test_get_access_token_force_refresh(mock_client_cls):
    first = _fake_jwt(exp=int(time.time()) + 3600)
    second = _fake_jwt(exp=int(time.time()) + 7200)
    mock_post = mock_client_cls.return_value.__enter__.return_value.post
    mock_post.side_effect = [
        MagicMock(status_code=200, json=lambda: {"accessToken": first, "refreshToken": "r1"}),
        MagicMock(status_code=200, json=lambda: {"accessToken": second, "refreshToken": "r2"}),
    ]

    client = CursorApiClient()
    assert client.get_access_token("crsr_test") == first
    assert client.get_access_token("crsr_test", force=True) == second
    assert mock_post.call_count == 2


@patch("httpx.Client")
def test_get_access_token_expired_cache_refreshes(mock_client_cls):
    expired = _fake_jwt(exp=int(time.time()) - 60)
    fresh = _fake_jwt(exp=int(time.time()) + 3600)
    mock_post = mock_client_cls.return_value.__enter__.return_value.post
    mock_post.side_effect = [
        MagicMock(status_code=200, json=lambda: {"accessToken": expired, "refreshToken": "r1"}),
        MagicMock(status_code=200, json=lambda: {"accessToken": fresh, "refreshToken": "r2"}),
    ]

    client = CursorApiClient(token_skew_seconds=300)
    assert client.get_access_token("crsr_test") == expired
    assert client.get_access_token("crsr_test") == fresh
    assert mock_post.call_count == 2


@patch("httpx.Client")
def test_post_dashboard_retries_on_401(mock_client_cls):
    jwt_token = _fake_jwt()
    refreshed = _fake_jwt(exp=int(time.time()) + 7200)
    mock_client = mock_client_cls.return_value.__enter__.return_value
    unauthorized = httpx.Response(401, request=MagicMock())
    success = MagicMock()
    success.status_code = 200
    success.raise_for_status = MagicMock()
    success.json.return_value = {"planUsage": {"totalSpend": 1}}
    mock_client.post.side_effect = [
        MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=lambda: {"accessToken": jwt_token, "refreshToken": "r"},
        ),
        httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=unauthorized),
        MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=lambda: {"accessToken": refreshed, "refreshToken": "r2"},
        ),
        success,
    ]

    client = CursorApiClient()
    token = client.get_access_token("crsr_test")
    data = client.get_current_period_usage(token, api_key="crsr_test")

    assert data["planUsage"]["totalSpend"] == 1
    assert mock_client.post.call_count == 4


def test_resolve_api_key_account_email_falls_back_to_get_me():
    client = CursorApiClient()
    token = _fake_jwt()
    client.exchange_user_api_key_response = MagicMock(
        return_value={"accessToken": token, "refreshToken": "ref"}
    )
    client.get_me = MagicMock(return_value={"email": "Feong@live.com"})

    assert client.resolve_api_key_account_email("crsr_test") == "feong@live.com"
    client.get_me.assert_called_once_with(token, api_key="crsr_test")
