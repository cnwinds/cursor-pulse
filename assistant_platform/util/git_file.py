"""Read file content and history from the enclosing git repository."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitFileRevision:
    commit: str
    committed_at: str
    subject: str


def find_repo_root(start: Path) -> Path | None:
    path = start.resolve()
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitFileError(f"git 不可用: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitFileError(detail or f"git 失败: {' '.join(args)}")
    return proc.stdout


def list_file_history(
    repo_root: Path,
    rel_path: str,
    *,
    limit: int = 40,
) -> list[GitFileRevision]:
    rel = rel_path.replace("\\", "/").lstrip("/")
    out = _run_git(
        repo_root,
        "log",
        f"-n{limit}",
        "--follow",
        "--format=%H%x09%cI%x09%s",
        "--",
        rel,
    )
    revisions: list[GitFileRevision] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        commit, committed_at, subject = line.split("\t", 2)
        revisions.append(
            GitFileRevision(commit=commit, committed_at=committed_at, subject=subject)
        )
    return revisions


def read_file_at_ref(repo_root: Path, rel_path: str, ref: str) -> str:
    rel = rel_path.replace("\\", "/").lstrip("/")
    spec = f"{ref}:{rel}"
    try:
        return _run_git(repo_root, "show", spec)
    except GitFileError as exc:
        if "exists on disk, but not in" in str(exc) or "does not exist" in str(exc):
            return ""
        raise


def merge_base(repo_root: Path, ref_a: str, ref_b: str) -> str:
    return _run_git(repo_root, "merge-base", ref_a, ref_b).strip()


def read_worktree_file(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if not path.is_file():
        raise GitFileError(f"工作区文件不存在: {rel_path}")
    return path.read_text(encoding="utf-8")
