from __future__ import annotations

from pathlib import Path

from assistant_platform.config import SkillsVectorConfig
from assistant_platform.memory.embedding import HashingEmbedder
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.skills.vector_index import SkillVectorIndex
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import SkillEmbeddingRow


def _write_docs(root: Path) -> None:
    docs = root / "docs"
    (docs / "cursor.self" / "tasks").mkdir(parents=True, exist_ok=True)
    (docs / "chat.smalltalk").mkdir(parents=True, exist_ok=True)
    (docs / "team.admin").mkdir(parents=True, exist_ok=True)

    (docs / "cursor.self" / "tasks" / "quota.md").write_text(
        "---\n"
        "name: 我的额度\n"
        "summary: 查询本人 Cursor 额度与用量明细\n"
        "when_to_use:\n"
        "  - 用户问额度 quota 用量 usage 还剩多少\n"
        "audience: [member]\n"
        "aliases: [额度, quota, 用量, usage]\n"
        "---\n"
        "# 我的额度\n"
        "查看本人 Cursor 额度 quota 与用量 usage 明细，剩余额度与消耗。\n",
        encoding="utf-8",
    )
    (docs / "chat.smalltalk" / "hello.md").write_text(
        "---\n"
        "name: 闲聊陪聊\n"
        "summary: 天气问候闲聊陪伴\n"
        "when_to_use:\n"
        "  - 用户想闲聊 天气 问候 心情\n"
        "audience: [member]\n"
        "aliases: [闲聊, 天气, 问候]\n"
        "---\n"
        "# 闲聊陪聊\n"
        "陪用户聊聊天气 心情 周末 电影 音乐 美食 旅行。\n",
        encoding="utf-8",
    )
    (docs / "team.admin" / "aggregate.md").write_text(
        "---\n"
        "name: 团队额度汇总\n"
        "summary: 汇总全团队 Cursor 额度用量\n"
        "when_to_use:\n"
        "  - 管理员查团队额度 quota 用量 usage 汇总\n"
        "audience: [admin]\n"
        "aliases: [团队额度, quota, usage]\n"
        "---\n"
        "# 团队额度汇总\n"
        "汇总全团队 Cursor 额度 quota 用量 usage 明细。\n",
        encoding="utf-8",
    )


def _member() -> SkillActorContext:
    return SkillActorContext("m1", "member", frozenset({"usage.self.read"}))


def _admin() -> SkillActorContext:
    return SkillActorContext("m1", "owner", frozenset({"usage.aggregate"}))


def _make_index(tmp_path: Path, db):
    registry = SkillRegistry(root=tmp_path)
    embedder = HashingEmbedder(dimensions=256)
    config = SkillsVectorConfig(enabled=True, score_threshold=0.15, top_k=3)
    return SkillVectorIndex(db, registry, embedder=embedder, config=config)


def test_sync_populates_embedding_rows(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    index = _make_index(tmp_path, db)

    index.sync()
    db.commit()

    rows = {r.skill_id: r for r in db.query(SkillEmbeddingRow).all()}
    assert set(rows) == {
        "cursor.self/tasks/quota",
        "chat.smalltalk/hello",
        "team.admin/aggregate",
    }
    quota = rows["cursor.self/tasks/quota"]
    assert quota.rel_path == "cursor.self/tasks/quota.md"
    assert quota.content_hash
    assert quota.embedding_json
    assert "member" in quota.audience_json
    db.close()


def test_route_cards_matches_query_over_smalltalk(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    index = _make_index(tmp_path, db)
    index.sync()
    db.commit()

    cards = index.route_cards("我的额度 quota 用量 usage 还剩多少", _member())
    ids = [c.skill_id for c in cards]
    assert "cursor.self/tasks/quota" in ids
    assert ids[0] == "cursor.self/tasks/quota"
    # Admin-only skill hidden from member even if semantically similar.
    assert "team.admin/aggregate" not in ids
    db.close()


def test_route_cards_admin_sees_admin_skill(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    index = _make_index(tmp_path, db)
    index.sync()
    db.commit()

    cards = index.route_cards("团队额度 quota 用量 usage 汇总", _admin())
    ids = [c.skill_id for c in cards]
    assert "team.admin/aggregate" in ids
    db.close()


def test_route_cards_offtopic_returns_empty(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    index = _make_index(tmp_path, db)
    index.sync()
    db.commit()

    cards = index.route_cards("zzzz qqqq wwww vvvv nonsense token", _member())
    assert cards == []
    db.close()


def test_sync_updates_on_content_change_and_deletes(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    index = _make_index(tmp_path, db)
    index.sync()
    db.commit()

    original = db.query(SkillEmbeddingRow).filter_by(
        skill_id="chat.smalltalk/hello"
    ).one()
    original_hash = original.content_hash

    # Change content → hash + embedding update on resync.
    (tmp_path / "docs" / "chat.smalltalk" / "hello.md").write_text(
        "---\nname: 闲聊\nsummary: 全新内容\naudience: [member]\n---\n"
        "# 闲聊\n完全不同的正文 内容 更新 后 的 文本。\n",
        encoding="utf-8",
    )
    # Delete a file → row removed on resync.
    (tmp_path / "docs" / "team.admin" / "aggregate.md").unlink()

    index2 = _make_index(tmp_path, db)
    index2.sync()
    db.commit()

    rows = {r.skill_id: r for r in db.query(SkillEmbeddingRow).all()}
    assert "team.admin/aggregate" not in rows
    assert rows["chat.smalltalk/hello"].content_hash != original_hash
    db.close()


def test_sync_reembeds_when_embedding_model_changes(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    registry = SkillRegistry(root=tmp_path)
    config = SkillsVectorConfig(enabled=True, score_threshold=0.15, top_k=3)

    index = SkillVectorIndex(
        db,
        registry,
        embedder=HashingEmbedder(dimensions=256),
        config=config,
        embedding_model="hashing-embedder",
    )
    stats = index.sync()
    db.commit()
    assert stats.upserted == 3
    assert stats.unchanged == 0

    row = db.query(SkillEmbeddingRow).filter_by(
        skill_id="chat.smalltalk/hello"
    ).one()
    assert row.embedding_model == "hashing-embedder"

    # Same file content, but the effective embedding model changed (e.g. a
    # HashingEmbedder algorithm bump, or switching to a real embedding API) —
    # content_hash alone must not be enough to skip re-embedding.
    resync = SkillVectorIndex(
        db,
        registry,
        embedder=HashingEmbedder(dimensions=256),
        config=config,
        embedding_model="hashing-embedder-v2",
    )
    stats2 = resync.sync()
    db.commit()
    assert stats2.upserted == 3
    assert stats2.unchanged == 0

    row2 = db.query(SkillEmbeddingRow).filter_by(
        skill_id="chat.smalltalk/hello"
    ).one()
    assert row2.embedding_model == "hashing-embedder-v2"

    # A third sync with the same (already up to date) model is a no-op.
    resync_again = SkillVectorIndex(
        db,
        registry,
        embedder=HashingEmbedder(dimensions=256),
        config=config,
        embedding_model="hashing-embedder-v2",
    )
    stats3 = resync_again.sync()
    db.commit()
    assert stats3.upserted == 0
    assert stats3.unchanged == 3
    db.close()


def test_sync_skips_embed_failures(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    registry = SkillRegistry(root=tmp_path)

    class FlakyEmbedder:
        def embed(self, text: str) -> list[float]:
            if "额度" in text:
                raise RuntimeError("boom")
            return HashingEmbedder(dimensions=256).embed(text)

    config = SkillsVectorConfig(enabled=True, score_threshold=0.15, top_k=3)
    index = SkillVectorIndex(db, registry, embedder=FlakyEmbedder(), config=config)
    index.sync()
    db.commit()

    rows = {r.skill_id for r in db.query(SkillEmbeddingRow).all()}
    # quota doc embed raised → skipped; others indexed.
    assert "cursor.self/tasks/quota" not in rows
    assert "chat.smalltalk/hello" in rows
    db.close()


def test_route_cards_disabled_returns_empty(tmp_path: Path):
    _write_docs(tmp_path)
    Session = init_assistant_db("sqlite://")
    db = Session()
    registry = SkillRegistry(root=tmp_path)
    embedder = HashingEmbedder(dimensions=256)
    config = SkillsVectorConfig(enabled=False, score_threshold=0.15, top_k=3)
    index = SkillVectorIndex(db, registry, embedder=embedder, config=config)
    index.sync()
    db.commit()

    cards = index.route_cards("我的额度 quota 用量", _member())
    assert cards == []
    db.close()
