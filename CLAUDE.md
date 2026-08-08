# Harness Eval

Package and CLI: `harness-eval`. "Crucible" and "Assay" were considered and
dropped — eval is understood on sight by anyone who's used lm-eval-harness or
OpenAI evals, no explaining required. In prose, calling a comparison run a
"battle" or a "war" between harnesses is fine and expected. The repo name
stays literal.

Controlled comparison of AI coding harnesses on a team's own repository.

## What this is

Runs the same task through Claude Code, Codex, Cursor, Aider and OpenCode in
isolated git worktrees, scores each on verifiable signals, and weights the
result by blast radius.

Two experiments live here and must not be confused:

- **Agent trial**: runtime fixed, model or prompt varies. Measures reasoning.
- **Harness trial**: model fixed, whole runtime varies. Measures the harness.

The second is the point. Published leaderboards vary both at once and then
attribute the whole delta to the model. Independent runs put the same model
15 to 25 points apart depending only on which harness wraps it.

## Non-negotiable invariants

Break any of these and the tool stops being worth running.

1. **No LLM judges code.** A run resolves only if the task's verify commands
   exit zero. Every other metric is counted off the diff. Never add a model
   scoring step to `oracle.py` or `scoring.py`.
2. **A missing measurement stays `None`.** Never default a token count or cost
   to zero. Reports must distinguish "not measured" from "free".
3. **One worktree per attempt.** Harnesses never share a tree. Cross
   contamination makes every number meaningless.
4. **Test tampering is an automatic fail.** A harness that edits the graded
   tests has moved the goalposts, regardless of exit codes.
5. **Adapters run things, they do not score.** Adapters return usage and exit
   codes. `oracle.py` decides what passed.
6. **Repeats are mandatory.** Agents are stochastic. Never report pass@1 from
   a single sample.

## Layout

```
harness_eval/
  models.py       Task, RunResult, OracleResult, BlastRadius, TrialReport
  adapters/
    base.py       HarnessAdapter ABC, subprocess plumbing, usage scraping
    impls.py      per-harness classes + shell escape hatch + REGISTRY
  mining/
    git_miner.py  merged commits -> graded tasks
  workspace.py    git worktree lifecycle
  oracle.py       verify commands, diff stats, tamper guard
  scoring.py      pass@k, Wilson intervals, blast-radius weighting
  runner.py       fan out task x harness x repeat
  report.py       markdown scorecard
  globs.py        ** aware path matching (see gotchas)
  config.py       YAML loading
  cli.py          doctor / mine / run / report
config/           harnesses.yaml, trials.yaml
tasks/samples/    calibration tasks for the fixture repo
scripts/          make_fixture.sh
tests/            31 tests, all passing
```

## Commands

```bash
pip install -e ".[dev]"
harness-eval doctor                    # which harness CLIs are on PATH
bash scripts/make_fixture.sh             # throwaway repo, 3 unsolved tasks
harness-eval run --tasks tasks/samples --repeats 2
harness-eval mine --repo ~/code/svc --since "6 months ago"
harness-eval run --tasks tasks/mined.yaml --blast-radius high
pytest -q && ruff check harness_eval
```

## Blast radius

The central concept. How much breaks if the change is wrong, and how hard it
is to undo. Sets repeat count and score weight.

**Important, easy to misread:** this is a property of the task, set once,
before any harness runs. It is not a comparison between running one harness
and running several. "Migrate the orders table to integer minor units" is
`high` whether you run it through one harness or five, because the danger is
in what a wrong migration does to downstream data, not in how many tools
attempted it. The tool then *uses* that danger rating to decide how much
scrutiny the task gets: more repeats, more weight in the scorecard. The
scrutiny is downstream of the rating. The rating itself has nothing to do
with harness count.

| | Repeats | Weight | Examples |
|---|---|---|---|
| low | 1 | 1x | one file, tests catch it, reverts in a commit |
| medium | 3 | 2x | a few modules, some shared code |
| high | 5 | 4x | migrations, auth, protobufs, terraform, shared contracts |

It is also the answer to "is running several harnesses worth it". Two
harnesses is roughly double the tokens, which is cheap next to engineering
time. The real cost is latency and reconciling two answers. Not worth it when
a change reverts in one commit. Worth it on a migration.

## Gotchas already hit and fixed

Do not reintroduce these.

- **`fnmatch` does not understand `**`.** `fnmatch("test_app.py", "**/test_*.py")`
  is `False`. This silently disabled the tamper guard for top-level test files.
  Always use `harness_eval.globs.matches_any`, never raw `fnmatch`.
- **`git log --numstat` emits stats after the pretty line.** A trailing record
  separator attaches every commit's file list to the previous commit, so every
  mined task came back with zero files. The separator now leads the format
  string in `_parse_log`. Do not move it back.
- **Root commits have no parent** and are correctly skipped by the miner. Test
  fixtures need at least two commits.

## Conventions

- No em dashes anywhere, code or prose.
- Comments explain why, not what. If a comment restates the line, delete it.
- Any change to `scoring.py` needs a test that would have failed before it.
- New harnesses go in `config/harnesses.yaml` via the `shell` adapter first.
  Write a Python adapter only for structured usage parsing or argv a template
  cannot express.
