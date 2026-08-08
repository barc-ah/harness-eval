"""Isolated workspaces, one per run.

Every attempt gets its own git worktree on a throwaway branch. Without this,
one harness's partial edits leak into the next harness's starting state and
the comparison is worthless. Isolation is not an optimisation here, it is the
thing that makes the numbers mean anything.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


# Bytecode and tool caches a harness's own self-checks (running its test
# suite, linting) leave behind. These are not source the harness touched and
# must never count as files changed or trip the test-tampering guard.
_ARTIFACT_EXCLUDES = [
    ":(exclude)**/__pycache__/**",
    ":(exclude)**/*.pyc",
    ":(exclude)**/*.pyo",
    ":(exclude)**/.pytest_cache/**",
    ":(exclude)**/.mypy_cache/**",
    ":(exclude)**/.ruff_cache/**",
]


def git(*args: str, cwd: Path, check: bool = True, timeout: int = 300):
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc


@dataclass
class Workspace:
    path: Path
    branch: str
    base_commit: str
    repo_root: Path

    def diff(self) -> str:
        """Uncommitted changes against the base commit, staged or not."""
        git("add", "-A", "-N", "--", ".", *_ARTIFACT_EXCLUDES, cwd=self.path, check=False)
        return git(
            "diff", "--no-color", "--", ".", *_ARTIFACT_EXCLUDES, cwd=self.path, check=False
        ).stdout

    def diff_stat(self) -> tuple[int, int, list[str]]:
        """Return (added, removed, files) using numstat so binaries do not lie."""
        git("add", "-A", "-N", "--", ".", *_ARTIFACT_EXCLUDES, cwd=self.path, check=False)
        out = git(
            "diff", "--numstat", "--", ".", *_ARTIFACT_EXCLUDES, cwd=self.path, check=False
        ).stdout
        added = removed = 0
        files: list[str] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, r, name = parts
            files.append(name)
            if a.isdigit():
                added += int(a)
            if r.isdigit():
                removed += int(r)
        return added, removed, files

    def reset(self) -> None:
        git("reset", "--hard", self.base_commit, cwd=self.path, check=False)
        git("clean", "-fdx", cwd=self.path, check=False)


def resolve_repo(repo: str) -> Path:
    path = Path(repo).expanduser().resolve()
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    return path


@contextmanager
def worktree(
    repo: str,
    base_commit: str,
    label: str,
    keep: bool = False,
    root: Path | None = None,
) -> Iterator[Workspace]:
    """Create a throwaway worktree, yield it, then tear it down.

    keep=True leaves the tree on disk so a failed run can be inspected by
    hand. That is usually the first thing you want when a harness scores
    unexpectedly low, since the diff explains more than the score does.
    """
    repo_root = resolve_repo(repo)
    root = root or Path.home() / ".harness-eval" / "worktrees"
    root.mkdir(parents=True, exist_ok=True)

    slug = f"{label}-{uuid.uuid4().hex[:8]}"
    wt_path = root / slug
    branch = f"trials/{slug}"

    git("worktree", "add", "--detach", str(wt_path), base_commit, cwd=repo_root)
    git("checkout", "-B", branch, cwd=wt_path)

    ws = Workspace(
        path=wt_path, branch=branch, base_commit=base_commit, repo_root=repo_root
    )
    try:
        yield ws
    finally:
        if not keep:
            git("worktree", "remove", "--force", str(wt_path), cwd=repo_root, check=False)
            git("branch", "-D", branch, cwd=repo_root, check=False)
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
