"""The oracle: what counts as a correct answer.

Nothing in here asks a model for an opinion. A run resolves if the task's
verify commands exit zero against the harness's diff. Everything else is
counted off the diff itself.

That constraint is deliberate. LLM judges drift on code, they reward
plausible looking output, and they will happily pass a change that deletes
the failing test. Exit codes do not have taste.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .globs import matches_any
from .models import OracleResult, Task
from .workspace import Workspace

TAIL_CHARS = 4000


def _run_shell(cmd: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_setup(task: Task, ws: Workspace, timeout: int = 900) -> bool:
    """Prepare the workspace. Failure here invalidates the run, not the harness."""
    for cmd in task.setup:
        try:
            proc = _run_shell(cmd, ws.path, timeout)
        except subprocess.TimeoutExpired:
            return False
        if proc.returncode != 0:
            return False
    return True


def evaluate(task: Task, ws: Workspace, timeout: int = 900) -> OracleResult:
    """Score one workspace after a harness has finished with it."""
    added, removed, files = ws.diff_stat()

    outside = 0
    if task.files_hint:
        for name in files:
            if not matches_any(name, task.files_hint):
                outside += 1

    result = OracleResult(
        lines_added=added,
        lines_removed=removed,
        files_touched=len(files),
        files_outside_hint=outside,
        diff_empty=not files,
    )

    tail = ""
    passed = bool(task.verify)
    for cmd in task.verify:
        try:
            proc = _run_shell(cmd, ws.path, timeout)
        except subprocess.TimeoutExpired:
            result.verify_exit_codes.append(124)
            passed = False
            tail += f"\n$ {cmd}\n[timeout after {timeout}s]"
            break
        result.verify_exit_codes.append(proc.returncode)
        tail += f"\n$ {cmd}\n{proc.stdout[-1500:]}{proc.stderr[-1500:]}"
        if proc.returncode != 0:
            passed = False
            break

    result.verify_passed = passed
    result.verify_stdout_tail = tail[-TAIL_CHARS:]
    return result


def guard_test_tampering(ws: Workspace, test_globs: list[str]) -> list[str]:
    """Return test files the harness modified.

    A harness that edits the tests it is being graded on has not solved the
    task, it has moved the goalposts. This is the single most common way a
    naive benchmark reports a false pass, so the runner treats any hit here
    as a failure regardless of exit codes.
    """
    _, _, files = ws.diff_stat()
    globs = test_globs or ["**/test_*.py", "**/*_test.py", "**/tests/**", "**/*.spec.*"]
    return [f for f in files if matches_any(f, globs)]
