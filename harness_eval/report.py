"""Reports.

The scorecard leads with weighted score rather than raw resolved rate,
because a harness that aces trivial tasks and fails every migration is not
the one you want on your repo. Cost and wall clock sit in the same table so
an expensive win cannot hide behind a single accuracy figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .models import RunResult, Task
from .scoring import HarnessScore, harness_effect, score_all


def _fmt(value, spec: str = ".1%", dash: str = "n/a") -> str:
    if value is None:
        return dash
    if isinstance(value, float) and spec.endswith("%"):
        return format(value, spec)
    return str(value)


def scorecard_markdown(
    runs: Sequence[RunResult], tasks: Sequence[Task], trial_id: str = ""
) -> str:
    scores = score_all(runs, tasks)
    lines: list[str] = []

    lines.append(f"# Harness Eval scorecard {trial_id}".rstrip())
    lines.append("")
    lines.append(
        f"{len(tasks)} tasks, {len(scores)} harnesses, {len(runs)} total attempts."
    )
    lines.append("")

    lines.append("## Ranking")
    lines.append("")
    lines.append(
        "| Harness | Model | Weighted | pass@1 | pass@3 | 95% CI | Median s | Cost/resolved | Median churn | Flaky |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in scores:
        ci = f"{s.ci_low:.0%}-{s.ci_high:.0%}"
        cost = f"${s.cost_per_resolved:.2f}" if s.cost_per_resolved else "n/a"
        lines.append(
            f"| {s.harness} | {s.model or '-'} | {s.weighted_score:.1%} | "
            f"{s.pass_at_1:.1%} | {s.pass_at_3:.1%} | {ci} | "
            f"{s.median_wall_clock:.0f} | {cost} | {s.median_churn:.0f} | {s.flakiness:.0%} |"
        )
    lines.append("")

    lines.append("## By blast radius")
    lines.append("")
    lines.append("| Harness | Low | Medium | High |")
    lines.append("|---|---|---|---|")
    for s in scores:
        b = s.per_blast_radius
        lines.append(
            f"| {s.harness} | {_fmt(b.get('low'))} | {_fmt(b.get('medium'))} | {_fmt(b.get('high'))} |"
        )
    lines.append("")
    lines.append(
        "High blast radius rows are the ones that justify running more than one "
        "harness. If a change reverts in a single commit, the second opinion is "
        "not worth its latency."
    )
    lines.append("")

    effects = harness_effect(scores)
    if effects:
        lines.append("## Harness effect, model held constant")
        lines.append("")
        lines.append("| Model | pass@1 spread across harnesses |")
        lines.append("|---|---|")
        for model, delta in sorted(effects.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {model} | {delta:.1%} |")
        lines.append("")
        lines.append(
            "Any non trivial spread here is capability you are leaving on the "
            "table by treating the harness as a UI preference."
        )
        lines.append("")

    lines.append("## Reliability")
    lines.append("")
    lines.append("| Harness | Attempts | Resolved | Timeouts | Errors | Scope violations |")
    lines.append("|---|---|---|---|---|---|")
    for s in scores:
        lines.append(
            f"| {s.harness} | {s.attempts} | {s.resolved} | {s.timeouts} | "
            f"{s.errors} | {s.scope_violations} |"
        )
    lines.append("")

    failures = [r for r in runs if not r.resolved and r.error]
    if failures:
        lines.append("## Failure notes")
        lines.append("")
        for r in failures[:20]:
            lines.append(f"- `{r.harness}` on `{r.task_id}` run {r.run_index}: {r.error}")
        lines.append("")

    return "\n".join(lines)


def write_report(
    runs: Sequence[RunResult],
    tasks: Sequence[Task],
    outdir: str | Path,
    trial_id: str = "",
) -> Path:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "scorecard.md"
    md.write_text(scorecard_markdown(runs, tasks, trial_id))
    return md


def print_summary(scores: Sequence[HarnessScore]) -> None:
    width = max((len(s.harness) for s in scores), default=8) + 2
    print(f"{'harness'.ljust(width)}{'weighted':>10}{'pass@1':>9}{'median s':>10}{'flaky':>8}")
    for s in scores:
        print(
            f"{s.harness.ljust(width)}"
            f"{s.weighted_score:>9.1%}"
            f"{s.pass_at_1:>9.1%}"
            f"{s.median_wall_clock:>10.0f}"
            f"{s.flakiness:>8.0%}"
        )
