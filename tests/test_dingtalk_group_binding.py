from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.channels.dingtalk.group_store import (
    load_persisted_group_binding,
    save_group_binding,
)
from pulse.storage.models import Base
from tests.conftest import make_team_repo


def test_save_and_load_group_binding_from_team_settings(tmp_path):
    db_path = tmp_path / "pulse.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = sf()
    team, _repo = make_team_repo(session)
    session.commit()

    save_group_binding(
        open_conversation_id="cid-db==",
        chat_id="123456",
        title="测试群",
        team_slug="test",
        database_url=db_url,
        session=session,
        team_id=team.id,
    )
    session.commit()
    session.close()

    binding = load_persisted_group_binding(team_slug="test", database_url=db_url)
    assert binding["open_conversation_id"] == "cid-db=="
    assert binding["chat_id"] == "123456"
    assert binding["title"] == "测试群"
