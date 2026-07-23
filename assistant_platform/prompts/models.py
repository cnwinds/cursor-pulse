from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromptFragmentRow(Base):
    __tablename__ = "ap_prompt_fragments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1")
    status: Mapped[str] = mapped_column(String(16), default="active")


class PromptReleaseRow(Base):
    __tablename__ = "ap_prompt_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    fragment_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PromptDeploymentRow(Base):
    __tablename__ = "ap_prompt_deployments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    release_id: Mapped[str] = mapped_column(String(36), index=True)
    percent: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(16), default="active")
