import subprocess
from pathlib import Path

import pytest

from harness_eval.adapters import build_adapter
from harness_eval.mining import mine
from harness_eval.models import BlastRadius, OracleResult, Outcome, RunResult, Task
from harness_eval.runner import Runner
from harness_eval.scoring import harness_effect, pass_at_k, score_all, wilson_interval
from harness_eval.workspace import worktree


def make_run(harness, task_id, resolved, idx=0, model=None, wall=10.0, cost=None):
    return RunResult(
        task_id=task_id,
        harness=harness,
        model=model,
        run_index=idx,
        outcome=Outcome.RESOLVED if resolved else Outcome.FAILED,
        oracle=OracleResult(verify_passed=resolved, lines_added=10, files_touched=2),
        wall_clock_sec=wall,
        cost_usd=cost,
    )


def make_task(tid, br=BlastRadius.MEDIUM):
    return Task(
        id=tid, prompt="p", repo=".", base_commit="HEAD", verify=["true"], blast_radius=br
    )


class TestPassAtK:
    def test_all_resolved(self):
        assert pass_at_k(5, 5, 1) == 1.0

    def test_none_resolved(self):
        assert pass_at_k(5, 0, 1) == 0.0

    def test_partial_is_between(self):
        v = pass_at_k(5, 2, 1)
        assert 0.3 < v < 0.5

    def test_k_increases_score(self):
        assert pass_at_k(5, 2, 3) > pass_at_k(5, 2, 1)

    def test_guards(self):
        assert pass_at_k(0, 0, 1) == 0.0
        assert pass_at_k(3, 1, 0) == 0.0


class TestWilson:
    def test_zero_trials(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_interval_contains_point(self):
        lo, hi = wilson_interval(7, 10)
        assert lo < 0.7 < hi

    def test_small_sample_is_wide(self):
        narrow = wilson_interval(70, 100)
        wide = wilson_interval(7, 10)
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


class TestScoring:
    def test_ranking_prefers_resolved(self):
        tasks = [make_task("t1"), make_task("t2")]
        runs = [
            make_run("good", "t1", True),
            make_run("good", "t2", True),
            make_run("bad", "t1", False),
            make_run("bad", "t2", False),
        ]
        scores = score_all(runs, tasks)
        assert scores[0].harness == "good"
        assert scores[0].pass_at_1 == 1.0
        assert scores[-1].pass_at_1 == 0.0

    def test_blast_radius_weighting(self):
        """A harness that wins the high blast radius task should rank higher."""
        tasks = [make_task("low", BlastRadius.LOW), make_task("high", BlastRadius.HIGH)]
        runs = [
            make_run("a", "low", False),
            make_run("a", "high", True),
            make_run("b", "low", True),
            make_run("b", "high", False),
        ]
        scores = {s.harness: s for s in score_all(runs, tasks)}
        assert scores["a"].weighted_score > scores["b"].weighted_score
        assert scores["a"].pass_at_1 == scores["b"].pass_at_1

    def test_flakiness_detected(self):
        tasks = [make_task("t1")]
        runs = [
            make_run("h", "t1", True, 0),
            make_run("h", "t1", False, 1),
            make_run("h", "t1", True, 2),
        ]
        s = score_all(runs, tasks)[0]
        assert s.flakiness == 1.0
        assert 0 < s.pass_at_1 < 1

    def test_cost_per_resolved(self):
        tasks = [make_task("t1")]
        runs = [
            make_run("h", "t1", True, 0, cost=2.0),
            make_run("h", "t1", False, 1, cost=2.0),
        ]
        s = score_all(runs, tasks)[0]
        assert s.total_cost_usd == 4.0
        assert s.cost_per_resolved == 4.0

    def test_missing_cost_is_none_not_zero(self):
        s = score_all([make_run("h", "t1", True)], [make_task("t1")])[0]
        assert s.total_cost_usd is None


class TestHarnessEffect:
    def test_spread_same_model(self):
        tasks = [make_task("t1"), make_task("t2")]
        runs = [
            make_run("cc", "t1", True, model="m1"),
            make_run("cc", "t2", True, model="m1"),
            make_run("cursor", "t1", False, model="m1"),
            make_run("cursor", "t2", True, model="m1"),
        ]
        effect = harness_effect(score_all(runs, tasks))
        assert effect["m1"] == pytest.approx(0.5)

    def test_single_harness_no_effect(self):
        runs = [make_run("cc", "t1", True, model="m1")]
        assert harness_effect(score_all(runs, [make_task("t1")])) == {}


class TestAdapters:
    def test_build_from_config(self):
        a = build_adapter({"name": "cc", "adapter": "claude-code", "model": "x"})
        assert a.name == "cc"
        assert a.model == "x"

    def test_command_includes_model(self):
        a = build_adapter({"name": "cc", "adapter": "claude-code", "model": "sonnet"})
        cmd = a.build_command("do the thing", Path("/tmp"))
        assert "--model" in cmd and "sonnet" in cmd
        assert "do the thing" in cmd

    def test_codex_prompt_is_positional_last(self):
        a = build_adapter({"name": "cx", "adapter": "codex"})
        cmd = a.build_command("hello", Path("/tmp"))
        assert cmd[-1] == "hello"
        assert cmd[1] == "exec"

    def test_unknown_adapter_rejected(self):
        with pytest.raises(ValueError):
            build_adapter({"name": "nope", "adapter": "does-not-exist"})

    def test_usage_scrape_leaves_missing_as_none(self):
        from harness_eval.adapters import scrape_usage

        u = scrape_usage("nothing useful here")
        assert u.input_tokens is None and u.cost_usd is None

    def test_usage_scrape_reads_numbers(self):
        from harness_eval.adapters import scrape_usage

        u = scrape_usage("input tokens: 1,200 output tokens: 340 total cost $0.42")
        assert u.input_tokens == 1200
        assert u.output_tokens == 340
        assert u.cost_usd == 0.42


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    # Root commit. The miner needs a parent to roll back to, so the graded
    # commit can never be the first one in the repo.
    (repo / "app.py").write_text("VERSION = '0.1'\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "initial scaffold for the project")
    (repo / "app.py").write_text("VERSION = '0.1'\n\n\ndef add(a, b):\n    return a + b\n")
    (repo / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "feat: add addition helper with tests")
    return repo


class TestWorkspace:
    def test_worktree_is_isolated(self, tiny_repo):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tiny_repo, capture_output=True, text=True
        ).stdout.strip()
        with worktree(str(tiny_repo), head, "iso") as ws:
            (ws.path / "scratch.txt").write_text("only here")
            assert not (tiny_repo / "scratch.txt").exists()
            added, removed, files = ws.diff_stat()
            assert "scratch.txt" in files
            assert added >= 1
        assert not (tiny_repo / "scratch.txt").exists()

    def test_diff_stat_empty_on_clean_tree(self, tiny_repo):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tiny_repo, capture_output=True, text=True
        ).stdout.strip()
        with worktree(str(tiny_repo), head, "clean") as ws:
            added, removed, files = ws.diff_stat()
            assert files == [] and added == 0 and removed == 0


class TestMining:
    def test_mines_commit_touching_source_and_tests(self, tiny_repo):
        tasks = mine(str(tiny_repo), since="10 years ago", max_tasks=5)
        assert len(tasks) == 1
        t = tasks[0]
        assert t.source == "mined"
        assert "addition helper" in t.prompt
        assert "Do not edit test files" in t.prompt

    def test_skips_commits_without_tests(self, tiny_repo):
        (tiny_repo / "README.md").write_text("docs\n")
        subprocess.run(["git", "add", "-A"], cwd=tiny_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "docs: write a much longer readme file"],
            cwd=tiny_repo, check=True, capture_output=True,
        )
        tasks = mine(str(tiny_repo), since="10 years ago", max_tasks=5)
        assert all("readme" not in t.prompt.lower() for t in tasks)

    def test_blast_radius_assigned(self, tiny_repo):
        tasks = mine(str(tiny_repo), since="10 years ago", max_tasks=5)
        assert tasks[0].blast_radius in {BlastRadius.LOW, BlastRadius.MEDIUM}


class TestRunner:
    def test_plan_scales_with_blast_radius(self):
        a = build_adapter({"name": "noop", "adapter": "noop"})
        tasks = [make_task("lo", BlastRadius.LOW), make_task("hi", BlastRadius.HIGH)]
        jobs = Runner([a]).plan(tasks)
        assert len(jobs) == 1 + 5

    def test_explicit_repeats_override(self):
        a = build_adapter({"name": "noop", "adapter": "noop"})
        jobs = Runner([a], repeats=2).plan([make_task("t", BlastRadius.HIGH)])
        assert len(jobs) == 2

    def test_missing_binary_is_skipped_not_failed(self):
        a = build_adapter({"name": "ghost", "adapter": "claude-code", "binary": "definitely-not-installed"})
        r = Runner([a]).run_one(make_task("t"), a, 0)
        assert r.outcome == Outcome.SKIPPED
        assert "not on PATH" in r.error

    def test_dry_run_touches_nothing(self):
        a = build_adapter({"name": "noop", "adapter": "noop"})
        report = Runner([a], dry_run=True).run([make_task("t")])
        assert all(r.outcome == Outcome.SKIPPED for r in report.runs)


class TestReport:
    def test_scorecard_renders(self):
        from harness_eval.report import scorecard_markdown

        tasks = [make_task("t1", BlastRadius.HIGH)]
        runs = [make_run("cc", "t1", True, model="m"), make_run("cx", "t1", False, model="m")]
        md = scorecard_markdown(runs, tasks, "abc123")
        assert "Ranking" in md
        assert "By blast radius" in md
        assert "Harness effect" in md
        assert "cc" in md and "cx" in md
