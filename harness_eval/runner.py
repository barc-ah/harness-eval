"""The runner. Fans one task out across several harnesses and repeats it.

The shape of a trial is deliberately boring: for each task, for each harness,
for each repeat, build a clean worktree, run the harness headlessly, score the
diff, throw the worktree away. Boring is the point. Any cleverness here shows
up later as an unexplained score difference.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from concurrent import futures
from datetime import datetime, timezone

from .adapters.base import HarnessAdapter
from .models import OracleResult, Outcome, RunResult, Task, TrialReport
from .oracle import evaluate, guard_test_tampering, run_setup
from .workspace import GitError, worktree

log = logging.getLogger("harness_eval")


class Runner:
    def __init__(
        self,
        adapters: Sequence[HarnessAdapter],
        repeats: int | None = None,
        concurrency: int = 1,
        keep_workspaces: bool = False,
        test_globs: Sequence[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.adapters = list(adapters)
        self.repeats = repeats
        self.concurrency = max(1, concurrency)
        self.keep_workspaces = keep_workspaces
        self.test_globs = list(test_globs or [])
        self.dry_run = dry_run

    # ------------------------------------------------------------------

    def repeats_for(self, task: Task) -> int:
        """How many attempts this task gets.

        Defaults scale with blast radius. A one file change that reverts in a
        commit does not need five samples. A schema migration does, because
        that is where being wrong is expensive and where a disagreement
        between harnesses is the signal you actually want.
        """
        return self.repeats or task.runs_needed

    def plan(self, tasks: Sequence[Task]) -> list[tuple[Task, HarnessAdapter, int]]:
        jobs = []
        for task in tasks:
            for adapter in self.adapters:
                for i in range(self.repeats_for(task)):
                    jobs.append((task, adapter, i))
        return jobs

    # ------------------------------------------------------------------

    def run_one(self, task: Task, adapter: HarnessAdapter, index: int) -> RunResult:
        label = f"{task.id}-{adapter.name}-{index}"
        base = RunResult(
            task_id=task.id,
            harness=adapter.name,
            model=adapter.model,
            run_index=index,
            outcome=Outcome.ERROR,
            oracle=OracleResult(),
            wall_clock_sec=0.0,
        )

        if self.dry_run:
            base.outcome = Outcome.SKIPPED
            return base

        if not adapter.available():
            base.error = f"binary not on PATH: {adapter.binary}"
            base.outcome = Outcome.SKIPPED
            return base

        try:
            with worktree(
                task.repo, task.base_commit, label, keep=self.keep_workspaces
            ) as ws:
                if not run_setup(task, ws):
                    base.error = "setup command failed"
                    base.workspace = str(ws.path)
                    return base

                inv = adapter.run(task.prompt, ws.path, timeout_sec=task.timeout_sec)
                base.wall_clock_sec = inv.wall_clock_sec
                base.exit_code = inv.exit_code
                base.workspace = str(ws.path)

                usage = adapter.parse_usage(inv)
                base.input_tokens = usage.input_tokens
                base.output_tokens = usage.output_tokens
                base.cost_usd = usage.cost_usd
                base.turns = usage.turns

                if inv.timed_out:
                    base.outcome = Outcome.TIMEOUT
                    base.oracle = evaluate(task, ws)
                    return base

                tampered = guard_test_tampering(ws, self.test_globs)
                oracle = evaluate(task, ws)
                base.oracle = oracle

                if tampered:
                    base.outcome = Outcome.FAILED
                    base.error = f"modified graded tests: {tampered[:5]}"
                    return base

                base.outcome = (
                    Outcome.RESOLVED if oracle.verify_passed else Outcome.FAILED
                )
                return base

        except GitError as exc:
            base.error = str(exc)
            return base
        except Exception as exc:  # noqa: BLE001
            base.error = f"{type(exc).__name__}: {exc}"
            return base

    # ------------------------------------------------------------------

    def run(self, tasks: Sequence[Task], progress=None) -> TrialReport:
        jobs = self.plan(tasks)
        started = datetime.now(timezone.utc).isoformat()
        report = TrialReport(
            trial_id=uuid.uuid4().hex[:10],
            started_at=started,
            finished_at=None,
            config_digest=TrialReport.digest(
                {
                    "adapters": [a.describe() for a in self.adapters],
                    "tasks": [t.id for t in tasks],
                    "repeats": self.repeats,
                }
            ),
            tasks=list(tasks),
        )

        if self.concurrency == 1:
            for task, adapter, i in jobs:
                result = self.run_one(task, adapter, i)
                report.runs.append(result)
                if progress:
                    progress(result, len(report.runs), len(jobs))
        else:
            # Concurrency is per worktree, so harnesses never share a tree.
            # Keep it modest: rate limits and disk churn bite before CPU does.
            with futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                pending = {
                    pool.submit(self.run_one, t, a, i): (t, a, i) for t, a, i in jobs
                }
                for fut in futures.as_completed(pending):
                    result = fut.result()
                    report.runs.append(result)
                    if progress:
                        progress(result, len(report.runs), len(jobs))

        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report
