from __future__ import annotations

from assistant_platform.secrets.store import delete_secret, get_secret, put_secret
from assistant_platform.storage.db import init_assistant_db

SECRET_KEY = "test-assistant-secret-key"


def test_put_get_secret_roundtrip():
    Session = init_assistant_db("sqlite://", team_id="team-secrets")
    session = Session()
    try:
        ref_id = put_secret(
            session,
            kind="cursor_api_key",
            plaintext="crsr_roundtrip_secret_key_abcdefghij",
            secret_key=SECRET_KEY,
        )
        session.commit()

        assert ref_id
        plaintext = get_secret(session, ref_id, secret_key=SECRET_KEY)
        assert plaintext == "crsr_roundtrip_secret_key_abcdefghij"
    finally:
        session.close()


def test_delete_secret():
    Session = init_assistant_db("sqlite://", team_id="team-secrets-del")
    session = Session()
    try:
        ref_id = put_secret(
            session,
            kind="cursor_api_key",
            plaintext="crsr_delete_me_key_abcdefghijklmnop",
            secret_key=SECRET_KEY,
        )
        session.commit()
        assert delete_secret(session, ref_id) is True
        session.commit()
        assert get_secret(session, ref_id, secret_key=SECRET_KEY) is None
    finally:
        session.close()
