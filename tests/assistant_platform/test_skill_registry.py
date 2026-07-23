from __future__ import annotations

from pathlib import Path

from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


def test_skill_actor_audiences_member_only():
    actor = SkillActorContext(
        member_id="m1",
        role="member",
        authorized_capability_keys=frozenset({"quota.self.read"}),
    )
    assert actor.audiences == frozenset({"member"})
    assert not actor.is_admin


def test_skill_actor_admin():
    actor = SkillActorContext(
        member_id="m1",
        role="owner",
        authorized_capability_keys=frozenset({"usage.aggregate"}),
    )
    assert actor.is_admin
    assert actor.audiences == frozenset({"member", "admin"})


def test_list_cards_filters_admin_skill_files():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    admin = SkillActorContext(
        "m1", "owner", frozenset({"usage.aggregate", "quota.self.read"})
    )
    member_ids = {c.skill_id for c in reg.list_cards(member)}
    admin_ids = {c.skill_id for c in reg.list_cards(admin)}
    assert "team.admin/overview" not in member_ids
    assert "team.admin/tasks/aggregate" not in member_ids
    assert "team.admin/overview" in admin_ids or any(
        i.startswith("team.admin/") for i in admin_ids
    )
    assert "cursor.self/tasks/quota" in member_ids or "cursor.self/overview" in member_ids


def test_load_docs_rejects_invisible_skill():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    try:
        reg.load_docs("team.admin/overview", actor=member)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "不可见" in str(exc)


def test_load_docs_admin_only_file_hidden_from_member():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    admin = SkillActorContext("m1", "owner", frozenset({"usage.aggregate"}))
    member = SkillActorContext("m1", "member", frozenset({"key.loan.request"}))
    admin_doc = reg.load_docs("key.loan/admin", actor=admin, token_budget=4000)
    assert "key_loan_list" in admin_doc.markdown
    try:
        reg.load_docs("key.loan/admin", actor=member, token_budget=4000)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "不可见" in str(exc)


def test_load_docs_injects_when_to_use_as_适用场景():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"usage.self.read"}))
    doc = reg.load_docs("cursor.self/tasks/my-usage", actor=member, token_budget=8000)
    assert "**适用场景**" in doc.markdown
    assert "我的用量" in doc.markdown


def test_load_docs_missing_file_raises():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    admin = SkillActorContext("m1", "owner", frozenset({"usage.aggregate"}))
    try:
        reg.load_docs("team.admin/does-not-exist", actor=admin)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "team.admin/does-not-exist" in str(exc)


def test_load_docs_window_metadata_for_short_file():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    doc = reg.load_docs("cursor.self/tasks/quota", actor=member, token_budget=8000)
    assert doc.total_lines > 0
    assert doc.start_line == 1
    assert doc.end_line == doc.total_lines
    assert doc.loaded_lines == doc.total_lines
    assert doc.has_more is False
    assert doc.next_start_line is None
    assert "**适用场景**" in doc.markdown


def test_load_docs_supports_start_line_continuation(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    body_lines = [f"line-{i}" for i in range(1, 251)]
    (docs / "long.md").write_text(
        "---\nname: Long\nsummary: long skill\naudience: [member]\n"
        "when_to_use:\n  - test\n---\n" + "\n".join(body_lines) + "\n",
        encoding="utf-8",
    )
    reg = SkillRegistry(root=tmp_path)
    actor = SkillActorContext("m1", "member", frozenset())
    first = reg.load_docs("long", actor=actor, start_line=1, max_lines=200)
    assert first.total_lines == 250
    assert first.loaded_lines == 200
    assert first.start_line == 1
    assert first.end_line == 200
    assert first.has_more is True
    assert first.next_start_line == 201
    assert "line-1" in first.markdown
    assert "line-200" in first.markdown
    assert "line-201" not in first.markdown

    second = reg.load_docs("long", actor=actor, start_line=201, max_lines=200)
    assert second.start_line == 201
    assert second.end_line == 250
    assert second.loaded_lines == 50
    assert second.has_more is False
    assert second.next_start_line is None
    assert "line-201" in second.markdown
    assert "line-250" in second.markdown
    assert "**适用场景**" not in second.markdown


def test_load_docs_start_line_past_end_raises(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "short.md").write_text(
        "---\naudience: [member]\n---\nonly\n",
        encoding="utf-8",
    )
    reg = SkillRegistry(root=tmp_path)
    actor = SkillActorContext("m1", "member", frozenset())
    try:
        reg.load_docs("short", actor=actor, start_line=5, max_lines=200)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "start_line" in str(exc) or "超出" in str(exc)


def test_scan_rejects_skill_id_path_mismatch(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "foo.md").write_text(
        "---\nskill_id: bar\naudience: [member]\n---\n# Foo\n",
        encoding="utf-8",
    )
    try:
        SkillRegistry(root=tmp_path)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "不一致" in str(exc)
        assert "bar" in str(exc)
        assert "foo" in str(exc)
