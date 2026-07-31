import logging

import pytest

from pulse.config import AppConfig, WebConfig
from pulse.storage.models import Member
from pulse.web import auth_tokens as auth_tokens_module
from pulse.web.auth_tokens import (
    assert_jwt_secret_configured,
    create_access_token,
    decode_access_token,
)


@pytest.fixture(autouse=True)
def reset_admin_token_fallback_warning():
    auth_tokens_module._admin_token_fallback_warned = False
    yield
    auth_tokens_module._admin_token_fallback_warned = False


@pytest.fixture
def member():
    m = Member(
        team_id="t1",
        channel_user_id="u1",
        display_name="Owner",
        status="active",
        portal_status="active",
        portal_role="owner",
    )
    m.id = "mem-1"
    return m


def test_create_access_token_default_ttl_is_30_minutes(member):
    config = AppConfig(web=WebConfig(jwt_secret="jwt-only"))
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    ttl = int(payload["exp"] - payload["iat"])
    assert 29 * 60 <= ttl <= 30 * 60


def test_create_access_token_respects_config_minutes(member):
    config = AppConfig(web=WebConfig(jwt_secret="jwt-only", access_token_minutes=15))
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    ttl = int(payload["exp"] - payload["iat"])
    assert 14 * 60 <= ttl <= 15 * 60


def test_hours_kwarg_still_supported(member):
    config = AppConfig(web=WebConfig(jwt_secret="jwt-only", access_token_minutes=30))
    token = create_access_token(config, member, hours=2)
    payload = decode_access_token(config, token)
    ttl = int(payload["exp"] - payload["iat"])
    assert 119 * 60 <= ttl <= 120 * 60


def test_refresh_token_hash_is_stable():
    from pulse.web.auth_tokens import create_refresh_token, hash_refresh_token

    raw = create_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)
    assert hash_refresh_token(raw) != hash_refresh_token(create_refresh_token())


def test_issue_and_rotate_refresh_token(member):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from pulse.storage.models import Base, PortalRefreshToken, Team
    from pulse.web.auth_tokens import (
        RefreshTokenError,
        hash_refresh_token,
        issue_token_pair,
        rotate_refresh_token,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    team = Team(slug="t", name="T")
    session.add(team)
    session.flush()
    member.team_id = team.id
    session.add(member)
    session.commit()

    config = AppConfig(web=WebConfig(jwt_secret="jwt-only", refresh_token_days=7))
    pair = issue_token_pair(session, config, member)
    session.commit()
    assert pair["expires_in"] == 30 * 60
    assert session.query(PortalRefreshToken).count() == 1

    rotated = rotate_refresh_token(session, config, pair["refresh_token"])
    session.commit()
    assert rotated["refresh_token"] != pair["refresh_token"]
    assert session.query(PortalRefreshToken).count() == 2
    old = session.query(PortalRefreshToken).filter_by(
        token_hash=hash_refresh_token(pair["refresh_token"])
    ).one()
    assert old.revoked_at is not None
    assert old.replaced_by_id is not None

    with pytest.raises(RefreshTokenError, match="reuse"):
        rotate_refresh_token(session, config, pair["refresh_token"])
    session.commit()
    # Reuse must not revoke the winner's new session (multi-tab safe).
    active = (
        session.query(PortalRefreshToken)
        .filter(PortalRefreshToken.revoked_at.is_(None))
        .count()
    )
    assert active == 1
    session.close()


def test_jwt_secret_preferred_over_admin_token(member):
    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-only", admin_token="admin-only"),
    )
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    assert payload["sub"] == "mem-1"


def test_admin_token_fallback_in_dev(member, monkeypatch):
    monkeypatch.delenv("PULSE_ENV", raising=False)
    config = AppConfig(web=WebConfig(admin_token="dev-admin-token"))
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    assert payload["sub"] == "mem-1"


def test_admin_token_fallback_logs_warning_once(member, monkeypatch, caplog):
    monkeypatch.delenv("PULSE_ENV", raising=False)
    config = AppConfig(web=WebConfig(admin_token="dev-admin-token"))
    with caplog.at_level(logging.WARNING):
        create_access_token(config, member)
        decode_access_token(config, create_access_token(config, member))
    warnings = [r for r in caplog.records if "JWT_SECRET" in r.message]
    assert len(warnings) == 1


def test_production_requires_jwt_secret(member, monkeypatch):
    monkeypatch.setenv("PULSE_ENV", "production")
    config = AppConfig(web=WebConfig(admin_token="admin-only"))
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_access_token(config, member)


def test_assert_jwt_secret_configured_rejects_production_without_secret(monkeypatch):
    monkeypatch.setenv("PULSE_ENV", "production")
    config = AppConfig(web=WebConfig(admin_token="admin-only"))
    with pytest.raises(ValueError, match="JWT_SECRET"):
        assert_jwt_secret_configured(config)


def test_assert_jwt_secret_configured_rejects_short_production_secret(monkeypatch):
    monkeypatch.setenv("PULSE_ENV", "production")
    config = AppConfig(web=WebConfig(jwt_secret="too-short"))
    with pytest.raises(ValueError, match="32 bytes"):
        assert_jwt_secret_configured(config)


def test_production_with_jwt_secret_works(member, monkeypatch):
    monkeypatch.setenv("PULSE_ENV", "production")
    config = AppConfig(web=WebConfig(jwt_secret="prod-jwt-secret-at-least-32-bytes!!"))
    token = create_access_token(config, member)
    payload = decode_access_token(config, token)
    assert payload["sub"] == "mem-1"
    assert_jwt_secret_configured(config)


def test_no_secret_raises(member, monkeypatch):
    monkeypatch.delenv("PULSE_ENV", raising=False)
    config = AppConfig(web=WebConfig())
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_access_token(config, member)


def test_create_app_rejects_production_without_jwt_secret(monkeypatch):
    pytest.importorskip("fastapi")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from pulse.storage.models import Base
    from pulse.web.app import create_app

    monkeypatch.setenv("PULSE_ENV", "production")
    config = AppConfig(web=WebConfig(admin_token="admin-only"))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        create_app(config, session_factory)
