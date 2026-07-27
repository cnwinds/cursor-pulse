from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import MagicMock

from pulse.storage.db import make_engine
from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base, Team


class SessionFactoryProxy:
    """Swap the bound sessionmaker without rebuilding FastAPI routes."""

    def __init__(self) -> None:
        self._sf = None

    def bind(self, sf) -> None:
        self._sf = sf

    def __call__(self, *args, **kwargs):
        if self._sf is None:
            raise RuntimeError("SessionFactoryProxy is not bound")
        return self._sf(*args, **kwargs)


def make_test_engine(database_url: str = "sqlite://") -> Engine:
    """SQLite engine for tests (memory by default; file URLs get WAL+NORMAL)."""
    return make_engine(database_url)


def make_test_session_factory(database_url: str = "sqlite://"):
    """create_all + migrate on a test engine; prefer over raw create_engine."""
    engine = make_test_engine(database_url)
    Base.metadata.create_all(engine)
    migrate_schema(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


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


def ingest_cursor_fixture(
    session: Session,
    *,
    team_id: str,
    account_id: str,
    vendor_id: str,
    member_id: str,
    period: str = "2026-07",
    fixture_name: str = "cursor_usage_events.json",
    event_index: int = 0,
):
    """Insert confirmed usage via Cursor API sync path (test helper)."""
    import json
    from pathlib import Path

    from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
    from pulse.ingestion.service import UsageIngestionService
    from pulse.ingestion.types import IngestionContext
    from pulse.integrations.cursor_api import map_usage_event

    raw = json.loads((Path(__file__).parent / "fixtures" / fixture_name).read_text())[
        "usageEventsDisplay"
    ][event_index]
    dto = map_usage_event(raw)
    context = IngestionContext(
        account_id=account_id,
        vendor_id=vendor_id,
        vendor_slug="cursor",
        billing_period=period,
        member_id=member_id,
        channel="test",
        source_type="api_sync",
        triggered_by=member_id,
        events=[dto],
        metadata={"source": "test"},
    )
    service = UsageIngestionService(session, team_id)
    return service.ingest(context=context, adapter=CursorApiAdapter())
