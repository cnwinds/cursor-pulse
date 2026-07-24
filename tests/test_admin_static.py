"""Admin SPA is packaged beside pulse.web (pulse/web/static)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from pulse.config import AppConfig, TenantConfig, WebConfig
from pulse.storage.models import Base
from pulse.web.app import create_app, resolve_admin_static_dir


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def test_resolve_admin_static_dir_override(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setenv("PULSE_ADMIN_STATIC_DIR", str(static))
    assert resolve_admin_static_dir() == static.resolve()


def test_resolve_admin_static_dir_empty_override_returns_none(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PULSE_ADMIN_STATIC_DIR", str(empty))
    assert resolve_admin_static_dir() is None


def test_require_admin_spa_raises_when_missing(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PULSE_ADMIN_STATIC_DIR", str(empty))
    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-test-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    with pytest.raises(RuntimeError, match="Vue admin SPA not found"):
        create_app(config, _session_factory(), require_admin_spa=True)


def test_admin_routes_serve_packaged_spa(monkeypatch, tmp_path):
    static = tmp_path / "static"
    assets = static / "assets"
    assets.mkdir(parents=True)
    (static / "index.html").write_text("<html>admin-shell</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("PULSE_ADMIN_STATIC_DIR", str(static))

    config = AppConfig(
        web=WebConfig(jwt_secret="jwt-test-secret"),
        tenant=TenantConfig(slug="test", name="Test"),
    )
    client = TestClient(create_app(config, _session_factory(), require_admin_spa=True))

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"].endswith("/admin/")

    page = client.get("/admin/")
    assert page.status_code == 200
    assert "admin-shell" in page.text

    deep = client.get("/admin/login")
    assert deep.status_code == 200
    assert "admin-shell" in deep.text

    asset = client.get("/admin/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text
