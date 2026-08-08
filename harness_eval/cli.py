"""Command line interface.

    harness-eval doctor                     check which harnesses are installed
    harness-eval mine --repo . --out tasks/mined.yaml
    harness-eval run --tasks tasks/samples --repeats 3
    harness-eval report --trial results/<id>.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_adapters, load_tasks, save_tasks
from .mining import mine
from .models import Task, TrialReport
from .report import print_summary, scorecard_markdown, write_report
from .runner import Runner
from .scoring import score_all


def _progress(result, done: int, total: int) -> None:
    mark = {"resolved": "ok", "failed": "no", "timeout": "to", "error": "er"}.get(
        result.outcome.value, "--"
    )
    print(
        f"[{done:>3}/{total}] {mark} {result.harness:<14} {result.task_id:<28} "
        f"{result.wall_clock_sec:.0f}s",
        flush=True,
    )


def cmd_doctor(args) -> int:
    adapters = load_adapters(args.config, only=args.harness)
    ok = 0
    for a in adapters:
        info = a.describe()
        status = "found" if info["available"] else "missing"
        print(f"{info['name']:<14} {status:<8} {info['binary']:<14} {info['version'] or ''}")
        ok += bool(info["available"])
    print(f"\n{ok}/{len(adapters)} harnesses available")
    return 0 if ok else 1


def cmd_mine(args) -> int:
    tasks = mine(
        repo=args.repo,
        since=args.since,
        limit=args.limit,
        max_tasks=args.max_tasks,
        verify=args.verify or ["pytest -q"],
        require_tests=not args.allow_untested,
    )
    if not tasks:
        print("no candidate commits found. try widening --since or --limit")
        return 1
    path = save_tasks(tasks, args.out)
    buckets: dict[str, int] = {}
    for t in tasks:
        buckets[t.blast_radius.value] = buckets.get(t.blast_radius.value, 0) + 1
    print(f"wrote {len(tasks)} tasks to {path}")
    print("blast radius: " + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    return 0


def cmd_run(args) -> int:
    adapters = load_adapters(args.config, only=args.harness)
    tasks = load_tasks(args.tasks)
    if args.task_id:
        tasks = [t for t in tasks if t.id in set(args.task_id)]
    if args.blast_radius:
        tasks = [t for t in tasks if t.blast_radius.value in set(args.blast_radius)]
    if not tasks:
        print("no tasks matched")
        return 1

    runner = Runner(
        adapters=adapters,
        repeats=args.repeats,
        concurrency=args.concurrency,
        keep_workspaces=args.keep,
        dry_run=args.dry_run,
    )
    total = len(runner.plan(tasks))
    print(
        f"{len(tasks)} tasks x {len(adapters)} harnesses = {total} attempts\n"
    )
    report = runner.run(tasks, progress=_progress)

    outdir = Path(args.out)
    report_path = report.save(outdir / f"{report.trial_id}.json")
    md = write_report(report.runs, tasks, outdir / report.trial_id, report.trial_id)

    print()
    print_summary(score_all(report.runs, tasks))
    print(f"\nraw: {report_path}\nscorecard: {md}")
    return 0


def cmd_report(args) -> int:
    data = TrialReport.load(args.trial)
    tasks = [Task.from_dict(t) for t in data["tasks"]]
    from .models import OracleResult, Outcome, RunResult

    runs = []
    for r in data["runs"]:
        runs.append(
            RunResult(
                task_id=r["task_id"],
                harness=r["harness"],
                model=r.get("model"),
                run_index=r["run_index"],
                outcome=Outcome(r["outcome"]),
                oracle=OracleResult(**r["oracle"]),
                wall_clock_sec=r["wall_clock_sec"],
                input_tokens=r.get("input_tokens"),
                output_tokens=r.get("output_tokens"),
                cost_usd=r.get("cost_usd"),
                turns=r.get("turns"),
                error=r.get("error"),
            )
        )
    text = scorecard_markdown(runs, tasks, data["trial_id"])
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness-eval",
        description="Controlled comparison of AI coding harnesses on your own repo.",
    )
    p.add_argument("--config", default="config/harnesses.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check installed harnesses")
    d.add_argument("--harness", action="append")
    d.set_defaults(func=cmd_doctor)

    m = sub.add_parser("mine", help="build tasks from git history")
    m.add_argument("--repo", default=".")
    m.add_argument("--since", default="6 months ago")
    m.add_argument("--limit", type=int, default=400)
    m.add_argument("--max-tasks", type=int, default=40)
    m.add_argument("--verify", action="append")
    m.add_argument("--allow-untested", action="store_true")
    m.add_argument("--out", default="tasks/mined.yaml")
    m.set_defaults(func=cmd_mine)

    r = sub.add_parser("run", help="run a trial")
    r.add_argument("--tasks", default="tasks/samples")
    r.add_argument("--harness", action="append")
    r.add_argument("--task-id", action="append")
    r.add_argument("--blast-radius", action="append", choices=["low", "medium", "high"])
    r.add_argument("--repeats", type=int, default=None)
    r.add_argument("--concurrency", type=int, default=1)
    r.add_argument("--keep", action="store_true", help="keep worktrees for inspection")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--out", default="results")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="rebuild a scorecard from raw results")
    rep.add_argument("--trial", required=True)
    rep.add_argument("--out")
    rep.set_defaults(func=cmd_report)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
