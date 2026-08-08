"""Aggregation and statistics.

Two ideas do most of the work here.

First, agents are stochastic. The same prompt against the same model produces
different code on different days, so a single run per task measures noise.
Every number below is computed over repeated attempts and reported with a
spread, never as a bare percentage.

Second, accuracy alone rewards whichever harness is willing to burn the most
tokens. Resolved rate is always reported next to cost and wall clock so a
slower, pricier win is visible as such.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .models import BlastRadius, RunResult, Task


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k from Chen et al.

    n = attempts made, c = attempts that resolved, k = attempts you would
    actually pay for in practice. Reporting pass@1 off a single sample is the
    most common way benchmark numbers become unreproducible.
    """
    if n <= 0 or k <= 0:
        return 0.0
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Honest about small sample sizes."""
    if trials == 0:
        return (0.0, 0.0)
    p = successes / trials
    denom = 1 + z**2 / trials
    centre = p + z**2 / (2 * trials)
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * trials)) / trials)
    return (max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom))


@dataclass
class HarnessScore:
    harness: str
    model: str | None = None
    attempts: int = 0
    resolved: int = 0
    tasks_attempted: int = 0
    tasks_any_resolved: int = 0
    pass_at_1: float = 0.0
    pass_at_3: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    weighted_score: float = 0.0
    median_wall_clock: float = 0.0
    p90_wall_clock: float = 0.0
    total_cost_usd: float | None = None
    cost_per_resolved: float | None = None
    total_tokens: int | None = None
    median_churn: float = 0.0
    median_files_touched: float = 0.0
    scope_violations: int = 0
    timeouts: int = 0
    errors: int = 0
    flakiness: float = 0.0
    per_blast_radius: dict[str, float] = field(default_factory=dict)

    @property
    def resolved_rate(self) -> float:
        return self.resolved / self.attempts if self.attempts else 0.0


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(pct * (len(ordered) - 1)))
    return float(ordered[idx])


def score_harness(
    harness: str,
    runs: Sequence[RunResult],
    tasks: Iterable[Task],
) -> HarnessScore:
    task_map = {t.id: t for t in tasks}
    mine = [r for r in runs if r.harness == harness]
    score = HarnessScore(harness=harness, attempts=len(mine))
    if not mine:
        return score

    score.model = next((r.model for r in mine if r.model), None)
    score.resolved = sum(1 for r in mine if r.resolved)
    score.timeouts = sum(1 for r in mine if r.outcome.value == "timeout")
    score.errors = sum(1 for r in mine if r.outcome.value == "error")

    by_task: dict[str, list[RunResult]] = defaultdict(list)
    for r in mine:
        by_task[r.task_id].append(r)

    score.tasks_attempted = len(by_task)
    score.tasks_any_resolved = sum(
        1 for rs in by_task.values() if any(r.resolved for r in rs)
    )

    p1: list[float] = []
    p3: list[float] = []
    flaky = 0
    weighted_num = weighted_den = 0.0
    br_buckets: dict[str, list[float]] = defaultdict(list)

    for task_id, rs in by_task.items():
        n = len(rs)
        c = sum(1 for r in rs if r.resolved)
        v1 = pass_at_k(n, c, 1)
        p1.append(v1)
        p3.append(pass_at_k(n, c, min(3, n)))
        if n > 1 and 0 < c < n:
            flaky += 1
        br = task_map.get(task_id).blast_radius if task_id in task_map else BlastRadius.MEDIUM
        weighted_num += v1 * br.weight
        weighted_den += br.weight
        br_buckets[br.value].append(v1)

    score.pass_at_1 = sum(p1) / len(p1)
    score.pass_at_3 = sum(p3) / len(p3)
    score.flakiness = flaky / len(by_task)
    score.weighted_score = weighted_num / weighted_den if weighted_den else 0.0
    score.per_blast_radius = {
        k: sum(v) / len(v) for k, v in br_buckets.items() if v
    }
    score.ci_low, score.ci_high = wilson_interval(score.resolved, score.attempts)

    walls = [r.wall_clock_sec for r in mine]
    score.median_wall_clock = _median(walls)
    score.p90_wall_clock = _percentile(walls, 0.9)

    costs = [r.cost_usd for r in mine if r.cost_usd is not None]
    if costs:
        score.total_cost_usd = round(sum(costs), 4)
        if score.resolved:
            score.cost_per_resolved = round(sum(costs) / score.resolved, 4)

    toks = [r.total_tokens for r in mine if r.total_tokens is not None]
    if toks:
        score.total_tokens = sum(toks)

    score.median_churn = _median([r.oracle.churn for r in mine])
    score.median_files_touched = _median([float(r.oracle.files_touched) for r in mine])
    score.scope_violations = sum(
        1 for r in mine if r.oracle.files_outside_hint > 0
    )
    return score


def score_all(runs: Sequence[RunResult], tasks: Iterable[Task]) -> list[HarnessScore]:
    tasks = list(tasks)
    names = sorted({r.harness for r in runs})
    scores = [score_harness(n, runs, tasks) for n in names]
    return sorted(scores, key=lambda s: (-s.weighted_score, s.median_wall_clock))


def harness_effect(scores: Sequence[HarnessScore]) -> dict[str, float]:
    """Spread attributable to the harness when the model is held constant.

    Group by model, then report the gap between best and worst harness inside
    each group. This is the number that public leaderboards cannot produce,
    because they change the model and the harness at the same time and then
    attribute the whole delta to the model.
    """
    by_model: dict[str, list[HarnessScore]] = defaultdict(list)
    for s in scores:
        by_model[s.model or "unspecified"].append(s)
    out = {}
    for model, group in by_model.items():
        if len(group) < 2:
            continue
        vals = [g.pass_at_1 for g in group]
        out[model] = round(max(vals) - min(vals), 4)
    return out
