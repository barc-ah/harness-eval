# Handoff

State as of the first commit. Everything below is verified, not assumed.

## Done and working

- 1,881 lines across 15 modules. `ruff check` clean, 31 tests passing.
- Six adapters registered: claude-code, codex, cursor, aider, opencode, shell,
  plus noop for dry runs.
- Git miner produces tasks from real history. Verified against a two-commit
  fixture.
- Worktree isolation verified: writes inside a trial do not touch the source
  repo, and the tree is removed afterwards.
- End to end trial run against `/tmp/harness-eval-fixture` with three fake
  harnesses. Solver resolved, lazy failed, cheater caught modifying the graded
  test. Scorecard rendered with ranking, blast radius breakdown, harness
  effect, reliability, and failure notes.
- CI workflow on 3.10 and 3.12 running ruff, pytest, and a dry run smoke test.

## Not done

No adapter has been run against a real harness CLI. The argv in `impls.py` is
written from documented flags, not from execution. **This is the first thing
to verify.**

## Backlog, in priority order

### 1. Validate real adapters

Run `harness-eval doctor`, then a single task per installed harness against
the fixture repo. Expect argv drift. Fix in `config/harnesses.yaml` via
`extra_args` where possible, in `impls.py` only when the shape is wrong.

Specifically unverified:
- `codex exec` flag names and whether the prompt is positional last
- `cursor-agent -p` and `--force` behaviour in a non-tty
- whether `aider --yes-always` fully suppresses prompts in CI
- `opencode run` model string format

Add an argv assertion test for each one you confirm.

### 2. Budget enforcement

`config/trials.yaml` declares `max_usd` and `max_wall_clock_min`. The runner
ignores both. Wire them in: track cumulative cost and elapsed time in
`Runner.run`, stop dispatching new jobs past either ceiling, and mark
undispatched jobs `SKIPPED` with a reason so the scorecard shows the trial was
truncated rather than complete.

### 3. Mined task validation

The miner currently emits tasks without checking they are solvable. Add a
`harness-eval validate --tasks` command that, per task, creates a worktree,
checks out the graded tests from the solution commit onto the parent, and
confirms the tests fail. A mined task whose tests already pass at the parent
is a false positive and must be dropped.

`apply_test_only` in `git_miner.py` exists for this and is currently unused.
Wire it into the runner's setup path too, otherwise mined tasks run against
the parent's old tests and everything passes trivially. **This is a
correctness bug, not a feature.** Treat it as priority 2b if you are mining
before validating.

### 4. Cost model for harnesses that do not report usage

Only Claude Code emits structured tokens and cost. The others fall through to
`scrape_usage`, which mostly returns `None`. Options: parse each CLI's session
log where one exists, or add a per-harness price table and estimate from
turn counts. Estimated numbers must be labelled as estimates in the report.
Do not silently mix measured and modelled costs in one column.

### 5. HTML scorecard

`report.py` produces markdown only. An HTML view with the harness effect chart
would make results shareable. Keep the markdown path as the source of truth.

### 6. Skill portability layer

The interesting long-term problem. Claude skills, Codex configs and Cursor
rules do not port. Today the only way to compare harnesses fairly is to give
each one its native configuration, which means you are comparing configured
setups rather than raw runtimes. Both comparisons are valid and the tool
should be able to express which one it is running. Sketch a `skills:` block
per harness in config and a translation shim before building anything.

## Known limits worth documenting rather than fixing

- Headless single-shot only. Says nothing about interactive recovery or how a
  harness improves once a team invests in its skills.
- Results are valid only for the harness versions and model snapshots in the
  trial's config digest.
- Mined task quality tracks commit message quality. Terse repos yield thin
  tasks.

## Where to start

```bash
cd harness-eval
pip install -e ".[dev]"
pytest -q
harness-eval doctor
bash scripts/make_fixture.sh
harness-eval run --tasks tasks/samples --harness claude-code --repeats 1 --keep
```

`--keep` leaves the worktree on disk. When a harness scores unexpectedly, the
diff explains more than the score does.
