from pulse.channels.dingtalk.group_store import (
    load_persisted_group_binding,
    save_group_binding,
)
from tests.conftest import make_team_repo, make_test_session_factory


def test_save_and_load_group_binding_from_team_settings(tmp_path):
    # Re-open via URL requires a shared file DB (not :memory:).
    db_url = f"sqlite:///{(tmp_path / 'pulse.db').as_posix()}"
    sf = make_test_session_factory(db_url)
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
