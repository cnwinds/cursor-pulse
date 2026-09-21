"""Three-pane merge view for comparing two text variants against a common base."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import difflib

LineRole = Literal["context", "change", "conflict"]

_CONFLICT_START = re.compile(r"^<<<<<<< ")
_CONFLICT_MID = re.compile(r"^=======\s*$")
_CONFLICT_END = re.compile(r"^>>>>>>> ")


@dataclass(frozen=True)
class MergeLine:
    text: str
    role: LineRole
    line_number: int


@dataclass(frozen=True)
class ThreeWayMergeView:
    left_label: str
    right_label: str
    left_lines: tuple[MergeLine, ...]
    right_lines: tuple[MergeLine, ...]
    result_lines: tuple[MergeLine, ...]
    result_text: str
    change_count: int
    conflict_count: int


def _split_lines(text: str) -> list[str]:
    if text == "":
        return []
    return text.splitlines()


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines)


def _merge_with_git(left: str, base: str, right: str) -> str:
    with (
        tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fl,
        tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fb,
        tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fr,
    ):
        fl.write(left)
        fb.write(base)
        fr.write(right)
        fl.flush()
        fb.flush()
        fr.flush()
        paths = (fl.name, fb.name, fr.name)
    try:
        proc = subprocess.run(
            ["git", "merge-file", "-p", paths[0], paths[1], paths[2]],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode in (0, 1):
            return proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        for path in paths:
            Path(path).unlink(missing_ok=True)
    return right


def _base_edit_spans(base: list[str], variant: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    sm = difflib.SequenceMatcher(None, base, variant)
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            spans.append((i1, i2))
    return spans


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _segment_for_base_span(
    base: list[str], variant: list[str], base_span: tuple[int, int]
) -> list[str]:
    i1, i2 = base_span
    sm = difflib.SequenceMatcher(None, base, variant)
    for tag, bi1, bi2, vj1, vj2 in sm.get_opcodes():
        if bi2 <= i1:
            continue
        if bi1 >= i2:
            break
        if tag == "equal":
            offset = i1 - bi1
            start = vj1 + offset
            return variant[start : start + (i2 - i1)]
        return variant[vj1:vj2]
    return []


def _conflict_base_indices(
    base: list[str], left: list[str], right: list[str]
) -> set[int]:
    left_spans = _base_edit_spans(base, left)
    right_spans = _base_edit_spans(base, right)
    conflict: set[int] = set()
    for ls in left_spans:
        for rs in right_spans:
            if not _spans_overlap(ls, rs):
                continue
            # Same single-span replacement on both sides → auto-merge
            l_seg = _segment_for_base_span(base, left, ls)
            r_seg = _segment_for_base_span(base, right, rs)
            if l_seg == r_seg:
                continue
            start = max(ls[0], rs[0])
            end = min(ls[1], rs[1])
            conflict.update(range(start, end))
    return conflict


def _roles_for_variant(
    base: list[str],
    variant: list[str],
    conflict_base: set[int],
) -> list[LineRole]:
    roles: list[LineRole] = []
    sm = difflib.SequenceMatcher(None, base, variant)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                base_i = i1 + offset
                roles.append("conflict" if base_i in conflict_base else "context")
            continue
        span_conflicts = any(idx in conflict_base for idx in range(i1, max(i2, i1 + 1)))
        roles.extend(["conflict" if span_conflicts else "change"] * (j2 - j1))
    if len(roles) < len(variant):
        roles.extend(["change"] * (len(variant) - len(roles)))
    return roles[: len(variant)]


def _merge_non_conflicting(
    base: list[str], left: list[str], right: list[str]
) -> list[str]:
    out = list(base)
    for variant in (left, right):
        sm = difflib.SequenceMatcher(None, base, variant)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if tag == "replace":
                out[i1:i2] = variant[j1:j2]
            elif tag == "insert":
                out[i1:i1] = variant[j1:j2]
            elif tag == "delete":
                del out[i1:i2]
    return out


def _parse_result_lines(merged: str) -> tuple[list[str], list[LineRole], int]:
    lines = _split_lines(merged)
    out_lines: list[str] = []
    roles: list[LineRole] = []
    conflicts = 0
    i = 0
    while i < len(lines):
        if _CONFLICT_START.match(lines[i]):
            conflicts += 1
            i += 1
            while i < len(lines) and not _CONFLICT_MID.match(lines[i]):
                out_lines.append(lines[i])
                roles.append("conflict")
                i += 1
            if i < len(lines) and _CONFLICT_MID.match(lines[i]):
                i += 1
            while i < len(lines) and not _CONFLICT_END.match(lines[i]):
                out_lines.append(lines[i])
                roles.append("conflict")
                i += 1
            if i < len(lines) and _CONFLICT_END.match(lines[i]):
                i += 1
            continue
        out_lines.append(lines[i])
        roles.append("context")
        i += 1
    return out_lines, roles, conflicts


def build_three_way_merge_view(
    *,
    left_text: str,
    base_text: str,
    right_text: str,
    left_label: str,
    right_label: str,
) -> ThreeWayMergeView:
    base = _split_lines(base_text)
    left = _split_lines(left_text)
    right = _split_lines(right_text)

    conflict_base = _conflict_base_indices(base, left, right)
    left_roles = _roles_for_variant(base, left, conflict_base)
    right_roles = _roles_for_variant(base, right, conflict_base)

    if conflict_base:
        merged_raw = _merge_with_git(left_text, base_text, right_text)
        result_lines_raw, result_roles, conflict_count = _parse_result_lines(merged_raw)
    else:
        merged_lines = _merge_non_conflicting(base, left, right)
        merged_raw = _join_lines(merged_lines)
        result_lines_raw = merged_lines
        result_roles = ["context"] * len(merged_lines)
        conflict_count = 0

    left_lines = tuple(
        MergeLine(text=txt, role=role, line_number=n)
        for n, (txt, role) in enumerate(zip(left, left_roles, strict=False), start=1)
    )
    right_lines = tuple(
        MergeLine(text=txt, role=role, line_number=n)
        for n, (txt, role) in enumerate(zip(right, right_roles, strict=False), start=1)
    )
    result_lines = tuple(
        MergeLine(text=txt, role=role, line_number=n)
        for n, (txt, role) in enumerate(
            zip(result_lines_raw, result_roles, strict=False), start=1
        )
    )

    change_count = sum(1 for r in left_roles + right_roles if r == "change")
    result_text = _join_lines(result_lines_raw)

    return ThreeWayMergeView(
        left_label=left_label,
        right_label=right_label,
        left_lines=left_lines,
        right_lines=right_lines,
        result_lines=result_lines,
        result_text=result_text,
        change_count=change_count,
        conflict_count=conflict_count,
    )


def merge_view_to_json(view: ThreeWayMergeView) -> dict:
    def _line(line: MergeLine) -> dict:
        return {
            "text": line.text,
            "role": line.role,
            "line_number": line.line_number,
        }

    return {
        "left_label": view.left_label,
        "right_label": view.right_label,
        "result_text": view.result_text,
        "change_count": view.change_count,
        "conflict_count": view.conflict_count,
        "left_lines": [_line(line) for line in view.left_lines],
        "right_lines": [_line(line) for line in view.right_lines],
        "result_lines": [_line(line) for line in view.result_lines],
    }
