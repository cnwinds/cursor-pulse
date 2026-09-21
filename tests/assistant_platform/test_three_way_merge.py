from __future__ import annotations

from assistant_platform.util.three_way_merge import build_three_way_merge_view


def test_auto_merge_non_overlapping_changes():
    base = "a\nb\nc\n"
    left = "a\nb2\nc\n"
    right = "a\nb\nc2\n"
    view = build_three_way_merge_view(
        left_text=left,
        base_text=base,
        right_text=right,
        left_label="left",
        right_label="right",
    )
    assert view.conflict_count == 0
    assert "b2" in view.result_text
    assert "c2" in view.result_text


def test_conflict_when_same_region_differs():
    base = "line1\nline2\n"
    left = "line1\nleft\n"
    right = "line1\nright\n"
    view = build_three_way_merge_view(
        left_text=left,
        base_text=base,
        right_text=right,
        left_label="left",
        right_label="right",
    )
    assert view.conflict_count >= 1
    assert any(line.role == "conflict" for line in view.left_lines)
    assert any(line.role == "conflict" for line in view.right_lines)
