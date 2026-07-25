from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from assistant_platform.jobs.claim import claim_next_job
from assistant_platform.storage.db import make_engine
from assistant_platform.storage.models import Base
from assistant_platform.storage.repository import AssistantRepository


def test_concurrent_claim_only_one_worker_gets_job():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "claim.db"
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
        Session = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        setup = Session()
        repo = AssistantRepository(setup)
        repo.add_job(
            job_type="reply.send",
            payload={"session_id": "s1", "message_id": "m1", "text": "hi"},
        )
        setup.commit()
        setup.close()

        results: list[str | None] = []
        lock = threading.Lock()

        def worker():
            session = Session()
            try:
                job = claim_next_job(session, blocked_session_ids=set())
                with lock:
                    results.append(job.id if job else None)
                if job:
                    job.status = "done"
                    session.commit()
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        claimed = [job_id for job_id in results if job_id]
        assert len(claimed) == 1
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)
        Path(tmp).rmdir()


def test_claim_skips_blocked_session_process():
    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-a", "message_id": "m1"},
    )
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-b", "message_id": "m2"},
    )
    db.commit()

    first = claim_next_job(db, blocked_session_ids={"sess-a"})
    assert first is not None
    assert first.payload_json["session_id"] == "sess-b"
    assert first.status == "processing"


def test_claim_allows_reply_while_session_blocked():
    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-a", "message_id": "m1"},
    )
    repo.add_job(
        job_type="reply.send",
        payload={"session_id": "sess-a", "message_id": "m2", "text": "进度", "kind": "interim"},
    )
    db.commit()

    blocked = claim_next_job(db, blocked_session_ids={"sess-a"})
    assert blocked is not None
    assert blocked.job_type == "reply.send"


def test_add_job_dedupes_reply_send_by_message_id():
    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    payload = {"session_id": "s1", "message_id": "msg-dup", "text": "hello"}
    first = repo.add_job(job_type="reply.send", payload=payload)
    second = repo.add_job(job_type="reply.send", payload=payload)
    db.commit()
    assert first.id == second.id


def test_claim_prefers_session_process_over_session_close():
    """Live turns must not wait behind older close/distill jobs."""
    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    repo.add_job(
        job_type="session.close",
        payload={"session_id": "sess-old"},
    )
    db.commit()
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-live", "message_id": "m1"},
    )
    db.commit()

    claimed = claim_next_job(db, blocked_session_ids=set())
    assert claimed is not None
    assert claimed.job_type == "session.process"
    assert claimed.payload_json["session_id"] == "sess-live"


def test_claim_respects_allowed_job_types():
    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-a", "message_id": "m1"},
    )
    repo.add_job(
        job_type="session.close",
        payload={"session_id": "sess-b"},
    )
    db.commit()

    from assistant_platform.jobs.claim import BACKGROUND_JOB_TYPES, INTERACTIVE_JOB_TYPES

    bg = claim_next_job(
        db, blocked_session_ids=set(), allowed_job_types=BACKGROUND_JOB_TYPES
    )
    assert bg is not None
    assert bg.job_type == "session.close"

    interactive = claim_next_job(
        db, blocked_session_ids=set(), allowed_job_types=INTERACTIVE_JOB_TYPES
    )
    assert interactive is not None
    assert interactive.job_type == "session.process"


def test_interactive_claim_not_starved_by_close_backlog():
    """SQL allow-list must not let 100+ older closes fill the claim window."""
    from assistant_platform.jobs.claim import INTERACTIVE_JOB_TYPES

    Session = __import__("assistant_platform.storage.db", fromlist=["init_assistant_db"]).init_assistant_db("sqlite://")
    db = Session()
    repo = AssistantRepository(db)
    for i in range(120):
        repo.add_job(job_type="session.close", payload={"session_id": f"close-{i}"})
    db.commit()
    repo.add_job(
        job_type="session.process",
        payload={"session_id": "sess-live", "message_id": "m-live"},
    )
    db.commit()

    claimed = claim_next_job(
        db,
        blocked_session_ids=set(),
        allowed_job_types=INTERACTIVE_JOB_TYPES,
    )
    assert claimed is not None
    assert claimed.job_type == "session.process"
    assert claimed.payload_json["session_id"] == "sess-live"
