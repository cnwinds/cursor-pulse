from sqlalchemy import select
from sqlalchemy.orm import Session
from unittest.mock import MagicMock

from pulse.storage.models import Team


def mock_cursor_key_exchange(mock_client: MagicMock, *, email: str | None = None) -> None:
    import base64
    import json
    import time

    from pulse.integrations.cursor_api import (
        _normalize_account_email,
        resolve_account_email_from_exchange,
    )

    payload: dict = {"exp": int(time.time()) + 3600}
    if email:
        payload["email"] = email
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{encoded}.sig"
    exchange = {
        "accessToken": token,
        "refreshToken": "ref",
    }
    mock_client.exchange_user_api_key_response.return_value = exchange

    def _resolve(_api_key, exchange=None):
        data = exchange or mock_client.exchange_user_api_key_response.return_value
        resolved = resolve_account_email_from_exchange(data)
        if resolved:
            return resolved
        access_token = data.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            return None
        get_me = getattr(mock_client, "get_me", None)
        if get_me is None:
            return None
        try:
            me = get_me(access_token, api_key=_api_key)
        except Exception:
            return None
        me_email = me.get("email") if isinstance(me, dict) else None
        if isinstance(me_email, str) and "@" in me_email:
            return _normalize_account_email(me_email)
        return None

    mock_client.resolve_api_key_account_email.side_effect = _resolve


def make_team(session: Session, slug: str = "test") -> Team:
    team = session.scalar(select(Team).where(Team.slug == slug))
    if team is None:
        team = Team(slug=slug, name=slug.title())
        session.add(team)
        session.flush()
    return team


def make_team_repo(session: Session, slug: str = "test"):
    team = make_team(session, slug)
    try:
        from pulse.storage.repository import Repository

        return team, Repository(session, team.id)
    except ImportError:
        return team, None
