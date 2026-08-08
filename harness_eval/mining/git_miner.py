"""Turn your own git history into a benchmark.

Public benchmarks measure how a harness does on someone else's code. Useful,
but it does not tell you how it will do on yours. Your merge history already
contains hundreds of graded exercises: a commit that changed source and tests
together is a task whose answer key you already shipped.

The recipe is simple. Take a merge commit that touched both source and tests.
Roll the source back to the parent, keep the new tests, and hand the harness
the PR title as the prompt. If the tests go green, the harness rebuilt what
your team built. If not, it did not.

The obvious failure mode: your commit messages are terrible. Mined tasks are
only as good as the prompt you can recover from them, which is why the miner
scores candidates and skips the ones with nothing to say.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..globs import matches_any
from ..models import BlastRadius, Task

DEFAULT_TEST_GLOBS = [
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
    "**/*.test.ts",
    "**/*.test.js",
    "**/*.spec.ts",
    "**/*_test.go",
]

# Paths whose breakage spreads beyond the team that changed them.
HIGH_BLAST_PATTERNS = [
    "**/migrations/**",
    "**/schema*",
    "**/*.proto",
    "**/openapi*",
    "**/auth/**",
    "**/security/**",
    "**/terraform/**",
    "**/*.tf",
    "**/helm/**",
    "**/k8s/**",
]

NOISE_PREFIXES = (
    "merge ",
    "revert ",
    "bump ",
    "chore(deps",
    "wip",
    "fixup",
    "typo",
    "formatting",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    ).stdout


@dataclass
class Candidate:
    sha: str
    parent: str
    subject: str
    body: str
    files: list[str]
    added: int
    removed: int

    @property
    def test_files(self) -> list[str]:
        return [
            f for f in self.files if matches_any(f, DEFAULT_TEST_GLOBS)
        ]

    @property
    def source_files(self) -> list[str]:
        tests = set(self.test_files)
        return [f for f in self.files if f not in tests]

    @property
    def blast_radius(self) -> BlastRadius:
        if any(matches_any(f, HIGH_BLAST_PATTERNS) for f in self.files):
            return BlastRadius.HIGH
        if len(self.source_files) > 6 or (self.added + self.removed) > 400:
            return BlastRadius.HIGH
        if len(self.source_files) > 2 or (self.added + self.removed) > 80:
            return BlastRadius.MEDIUM
        return BlastRadius.LOW


def _parse_log(repo: Path, since: str, limit: int) -> list[Candidate]:
    sep = "\x1e"
    # The record marker leads the format on purpose. With --numstat, git emits
    # the stat block *after* the pretty line, so a trailing marker attaches
    # every commit's file list to the previous commit. That off-by-one is
    # invisible until you notice every mined task has no files.
    fmt = f"\x1d%H{sep}%P{sep}%s{sep}%b"
    raw = _git(
        repo,
        "log",
        f"--since={since}",
        f"--max-count={limit}",
        "--numstat",
        f"--pretty=format:{fmt}",
    )
    candidates: list[Candidate] = []
    for chunk in raw.split("\x1d"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        header, _, stats = chunk.partition("\n")
        parts = header.split(sep)
        if len(parts) < 3:
            continue
        sha, parents, subject = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        parent = parents.split()[0] if parents.split() else ""
        if not parent:
            continue
        files, added, removed = [], 0, 0
        for line in stats.splitlines():
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            a, r, name = cols
            files.append(name)
            added += int(a) if a.isdigit() else 0
            removed += int(r) if r.isdigit() else 0
        candidates.append(
            Candidate(sha.strip(), parent, subject.strip(), body.strip(), files, added, removed)
        )
    return candidates


def _clean_prompt(c: Candidate) -> str | None:
    subject = re.sub(r"\(#\d+\)\s*$", "", c.subject).strip()
    subject = re.sub(r"^(feat|fix|refactor|perf)(\(.+?\))?:\s*", "", subject, flags=re.IGNORECASE)
    if len(subject) < 12:
        return None
    if subject.lower().startswith(NOISE_PREFIXES):
        return None
    body = "\n".join(
        l for l in c.body.splitlines()
        if l.strip() and not l.startswith(("Co-authored-by", "Signed-off-by", "Reviewed-by"))
    )[:800]
    prompt = subject if not body else f"{subject}\n\n{body}"
    return (
        f"{prompt}\n\n"
        "Implement this change in the current repository. The relevant tests "
        "already exist and must pass unmodified. Do not edit test files."
    )


def mine(
    repo: str,
    since: str = "6 months ago",
    limit: int = 400,
    max_tasks: int = 40,
    verify: list[str] | None = None,
    require_tests: bool = True,
) -> list[Task]:
    """Extract runnable tasks from history.

    Only commits that touched tests and source together survive by default.
    Without a test change there is no answer key, and a task with no answer
    key is an opinion poll.
    """
    repo_path = Path(repo).expanduser().resolve()
    verify = verify or ["pytest -q"]
    tasks: list[Task] = []

    for c in _parse_log(repo_path, since, limit):
        if require_tests and not c.test_files:
            continue
        if not c.source_files:
            continue
        if c.added + c.removed > 1500:
            continue  # too large to be a single coherent instruction
        prompt = _clean_prompt(c)
        if not prompt:
            continue

        tasks.append(
            Task(
                id=f"mined-{c.sha[:10]}",
                prompt=prompt,
                repo=str(repo_path),
                base_commit=c.parent,
                verify=verify,
                blast_radius=c.blast_radius,
                files_hint=sorted({str(Path(f).parent / "*") for f in c.source_files})[:20],
                source="mined",
                metadata={
                    "sha": c.sha,
                    "subject": c.subject,
                    "test_files": c.test_files[:10],
                    "lines_changed": c.added + c.removed,
                },
            )
        )
        if len(tasks) >= max_tasks:
            break

    return tasks


def apply_test_only(repo: Path, sha: str, test_files: list[str]) -> None:
    """Restore the graded tests from the solution commit into a rolled back tree.

    Call this after checking out the parent commit. It is what makes the task
    solvable: the tests describe the target behaviour, the source does not
    implement it yet.
    """
    if not test_files:
        return
    subprocess.run(
        ["git", "checkout", sha, "--", *test_files],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
