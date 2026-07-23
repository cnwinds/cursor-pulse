from __future__ import annotations

from assistant_platform.prompts.compose import compose_system_supplement, load_prompt_fragments
from assistant_platform.prompts.loader import load_prompt_fragments_from_files


def test_load_prompt_fragments_from_files_has_heart_and_precepts():
    fragments = load_prompt_fragments_from_files()
    assert "heart.md" in fragments
    assert "precepts.md" in fragments
    assert "小脉" in fragments["heart.md"]
    assert "人设与表达" in fragments["precepts.md"]


def test_compose_system_supplement_reads_files_without_db():
    supplement = compose_system_supplement(None, None)
    assert "人设与语气补充" in supplement
    assert "precepts.md" in supplement
    assert "小脉" in supplement


def test_load_prompt_fragments_ignores_release_id():
    # Backward-compatible signature; must not require DB
    fragments = load_prompt_fragments(None, "any-id")
    assert "heart.md" in fragments
