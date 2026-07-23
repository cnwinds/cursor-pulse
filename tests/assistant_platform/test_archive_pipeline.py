from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from assistant_platform.config import AssistantConfig, AssistantChatMemoryConfig, MemoryArchiveConfig, MemoryFeatureFlags
from assistant_platform.conversation.models import ChatMessageRow
from assistant_platform.conversation.orchestrator import process_session_close_job
from assistant_platform.conversation.session_store import attach_user_message, close_session
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.memory.archive_pipeline import (
    get_stage_status,
    run_archive_pipeline,
    run_archive_pipeline_stage,
)
from assistant_platform.memory.contracts import ArchivePipelineStage, ArchivePipelineStatus
from assistant_platform.memory.session_summary import SessionSummaryRow, load_session_summary
from assistant_platform.profiles.models import ProfileEffectiveRow, ProfileSignalRow
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.memory.semantic.models import SemanticAtomRow

TEAM_ID = "team-pipeline"


def _config(**overrides) -> AssistantConfig:
    base = AssistantConfig(
        team_id=TEAM_ID,
        apply_team_settings_overrides=False,
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=True),
            features=MemoryFeatureFlags(
                archive_pipeline=True,
                distill_on_close=True,
                profile_compile=True,
            ),
        ),
    )
    return base.model_copy(update=overrides) if overrides else base


def _event(*, text: str = "偏好: 简洁回复", sender: str = "u1") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=sender,
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id=sender,
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _closed_session(session, *, text: str = "偏好: 简洁回复"):
    session_row, _ = attach_user_message(session, _event(text=text))
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted="好的，我会尽量简洁。",
            meta_json={"kind": "final"},
        )
    )
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    session.commit()
    return session_row


def test_archive_pipeline_stages_complete_idempotently():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _config()
    session_row = _closed_session(session, text="事实: 使用 Opus")
    session.add(
        ChatMessageRow(
            session_id=session_row.id,
            role="user",
            text_redacted="偏好: 简洁列表",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    run_archive_pipeline(session, config=config, session_row=session_row)
    session.commit()

    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert archive is not None
    assert archive.archive_status == "ready"
    assert archive.index_status == "ready"
    for stage in ArchivePipelineStage:
        assert get_stage_status(archive, stage).status == ArchivePipelineStatus.READY

    summary = load_session_summary(session, session_row.id)
    assert summary is not None
    assert summary.user_goal
    assert len(summary.preferences) >= 1

    # second run is a no-op for ready stages
    run_archive_pipeline(session, config=config, session_row=session_row)
    session.commit()
    archive2 = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert archive2.content_hash == archive.content_hash
    session.close()


def test_single_stage_retry_after_failure():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _config()
    session_row = _closed_session(session)

    run_archive_pipeline_stage(
        session,
        config=config,
        session_row=session_row,
        stage=ArchivePipelineStage.ARCHIVE,
    )
    session.commit()

    from unittest.mock import patch

    with patch(
        "assistant_platform.memory.archive_pipeline.index_archived_session",
        side_effect=RuntimeError("index boom"),
    ):
        with pytest.raises(RuntimeError):
            run_archive_pipeline_stage(
                session,
                config=config,
                session_row=session_row,
                stage=ArchivePipelineStage.INDEX,
            )
        session.commit()

    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert archive is not None
    assert get_stage_status(archive, ArchivePipelineStage.INDEX).status == ArchivePipelineStatus.FAILED

    run_archive_pipeline_stage(
        session,
        config=config,
        session_row=session_row,
        stage=ArchivePipelineStage.INDEX,
    )
    session.commit()
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert archive.index_status == "ready"
    session.close()


def test_process_session_close_job_runs_pipeline_and_profile():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _config()
    session_row = _closed_session(session, text="偏好: 我习惯用 Opus 模型")

    process_session_close_job(session, {"session_id": session_row.id}, config)
    session.commit()

    signal = session.scalar(select(ProfileSignalRow).where(ProfileSignalRow.user_id == "u1"))
    assert signal is not None
    assert signal.explicitness == "explicit"
    assert session_row.id in signal.source_session_ids_json

    effective = session.scalar(
        select(ProfileEffectiveRow).where(
            ProfileEffectiveRow.user_id == "u1",
            ProfileEffectiveRow.team_id == TEAM_ID,
        )
    )
    assert effective is not None
    assert effective.snapshot_json.get("items")

    atom = session.scalar(select(SemanticAtomRow).where(SemanticAtomRow.subject_id == "u1"))
    assert atom is not None
    assert (atom.evidence_json or {}).get("session_ids")

    summary_row = session.scalar(
        select(SessionSummaryRow).where(SessionSummaryRow.session_id == session_row.id)
    )
    assert summary_row is not None
    session.close()


def test_facts_stage_skipped_without_distill_on_close():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _config(
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=True),
            features=MemoryFeatureFlags(
                archive_pipeline=True,
                distill_on_close=False,
                profile_compile=True,
            ),
        ),
    )
    session_row = _closed_session(session, text="事实: 使用 Opus")
    session.commit()

    run_archive_pipeline(session, config=config, session_row=session_row)
    session.commit()

    assert session.scalar(select(SemanticAtomRow).where(SemanticAtomRow.subject_id == "u1")) is None
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    assert get_stage_status(archive, ArchivePipelineStage.FACTS).status == ArchivePipelineStatus.READY
    session.close()


def test_group_close_skips_personal_profile():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    group_event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="group",
        conversation_id="g1",
        text_redacted="偏好: 群里不说",
        occurred_at=datetime.now(timezone.utc),
    )
    session_row, _ = attach_user_message(session, group_event)
    close_session(session, session_row, reason="manual", enqueue_close_job=False)
    session.commit()

    run_archive_pipeline(session, config=_config(), session_row=session_row)
    session.commit()

    assert session.scalar(select(ProfileSignalRow)) is None
    session.close()
