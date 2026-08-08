# You Are Benchmarking the Wrong Thing

## Your coding agent's runtime matters more than its model, and nobody is measuring it on your code

I spent most of last year picking coding agents the way everyone else does. Read the SWE-bench number. Read someone's thread. Try it for a week. Form an opinion. Move on.

Then I started running the same task through two tools at once, and the opinions stopped holding up.

Not because one tool was better. Because the gap between them kept moving depending on what I asked, and I had no way to tell whether I was looking at a real difference or at Tuesday.

## The number that should bother you

Endor Labs ran a benchmark comparing coding agents on functionality and security. GPT-5.5 scored 61.5% on functionality inside its own Codex harness. The same model, the same week, running inside Cursor's harness: 87.2%.

Twenty five points. No model update. No fine tuning. Different runtime.

The Opus result is worse, in the sense of being more awkward. Opus 4.7 scored 91.1% in Cursor against 87.2% in Claude Code, which is Anthropic's model doing better in a competitor's harness than in Anthropic's own. Matt Mayer found a similar shape independently: 77% in Claude Code, 93% in Cursor, same model, same tasks. CORE-Bench found Opus at 42% with a minimal scaffold and 78% inside a full harness.

Sam Altman said it was hard to overstate how critical the harness is. That reads like vendor talk until you see the spread.

So here is the uncomfortable version. If your model choice moves you five points and your harness choice moves you twenty, you have been optimizing the small variable. Every "which model is best for coding" thread you have read was measuring two things and reporting one.

## Two different experiments, constantly confused

Once you see it, the terminology problem shows up everywhere.

**Agent trial:** hold the runtime fixed, change the model or the prompt. Claude Code running Sonnet against Claude Code running Opus. You are measuring reasoning.

**Harness trial:** hold the model fixed, change everything around it. Claude Code against Codex against Cursor, all pointed at the same model. Now you are measuring the runtime: how it edits files, what it keeps in context, whether it spawns subagents, how it runs tests, what its skill system can do.

These get collapsed into one conversation constantly, and the collapse is why benchmark arguments never resolve. Two people comparing "Claude versus GPT for coding" are usually comparing four things and agreeing on none of them.

The harness trial is the harder one and the one I care about, because the harness carries capability the model does not have on its own. Skills, MCP servers, repo indexing, sandboxing, the test loop. Strip those away and you get the CORE-Bench result: a very good model at 42%.

## The tooling exists, the measurement does not

Running several harnesses at once is a solved problem. Conductor has a multi model mode that runs the same prompt across models and shows you the outputs. Warp puts each agent in its own worktree and diffs them side by side. VS Code now surfaces Claude, Codex, and Copilot sessions in one view. Garry Tan called the whole category the harness wars, though he meant the market fight over lock-in rather than head to head task comparison.

All of it stops at the same place: you get two diffs and your eyes.

That is fine for one decision. It does not accumulate. You cannot tell in March whether the tool you picked in January is still the right one, you cannot tell whether a harness is 70% reliable or 90% reliable on the kind of work your team actually does, and you certainly cannot tell whether the difference you noticed last Thursday was real.

The academic work is further along than the tooling. Claw-SWE-Bench makes the argument directly: a resolved rate conflates the model, the harness, and the task set, and prior SWE-bench style evaluations never separated them because scaffolds, prompts, budgets and stopping rules all varied per system. A recent position paper splits the terms cleanly, distinguishing the agent harness (a model with tools working one task) from the system harness (the outer thing that turns goals into tasks and routes them).

Good framing. Still generic tasks on someone else's repositories.

## So I built the boring version

It is called Harness Eval. Python, Apache 2.0, and deliberately unexciting.

For each task, for each harness, for each repeat: make a clean git worktree, run the harness headlessly, score the diff, throw the worktree away. That is the whole design. Any cleverness in that loop shows up later as an unexplained score difference, which defeats the point.

Four decisions did most of the work.

**No model judges another model's code.** A run resolves if the task's verify commands exit zero. Everything else gets counted off the diff: lines added, files touched, files outside the expected scope, wall clock, tokens where the CLI reports them. LLM judges drift on code and reward output that looks right. Exit codes do not have taste.

There is a specific failure this prevents. A harness that cannot solve a task can always solve it by editing the test. The runner checks whether any graded test file changed and fails the run outright if so, regardless of what the exit codes say. My first end to end run had a deliberate cheater harness in it, and watching it get caught was more satisfying than it should have been.

**Repeats are not optional.** Same prompt, same model, different code tomorrow. One run per task measures noise. Everything is reported as pass@k with a Wilson interval, and there is a flakiness column counting tasks a harness solved on some attempts and not others. That column has turned out to be more useful than the headline number. A harness at 70% with 40% flakiness is a coin flip you are paying to re-run. A harness at 70% flat is a tool.

**The benchmark comes from your own git log.** This is the part I would keep if I had to throw the rest away. Your merge history is already a graded exam. A commit that changed source and tests together is a task whose answer key you shipped months ago. Roll the source back to the parent, keep the new tests, hand the harness the commit message as the prompt. Tests go green, it rebuilt what your team built.

Mined tasks use your libraries, your layout, your conventions. No public benchmark can offer that. The honest limitation is that they are only as good as the prompt you can recover from a commit message, so repos with terse history yield thin tasks, and the miner drops candidates with nothing to say. Run it on a busy repo and you will get far fewer tasks than commits. That is the tool working, not failing.

**Blast radius drives everything.** This one came out of a conversation where someone asked me what the whole thing costs, and I gave a number I had not measured. Fair hit. Let me correct it properly.

Running two harnesses is roughly double the tokens. Tokens are cheap next to engineering time, so cost is not the binding constraint and I should not have led with it. The real cost is latency and the overhead of reconciling two answers when they disagree.

Neither is worth paying on a change that reverts in one commit. Both are obviously worth paying on a schema migration.

So blast radius is the trigger, not task size and not spend. How much breaks if this is wrong, and how hard is it to undo. In the tool it sets both the repeat count and the weight in the final score: low blast radius gets one attempt and 1x weight, high blast radius gets five attempts and 4x. A harness that aces trivial edits and fails every migration should not top your scorecard, and without weighting it will.

## What a scorecard actually tells you

Rank by weighted score. Then look at three things, none of which are the headline number.

The **harness effect** row is the point of the exercise: the spread across harnesses when the model is held constant. If it is large on your repo, your tool choice is doing more work than your model choice and you have been picking on vibes.

**Flakiness**, as above.

**Scope violations**, meaning files touched outside where the change belonged. A harness can score well by rewriting more than it should. That passes CI and fails review, and the score alone will not show it.

## What this does not measure

Headless single shot performance is not the same as the experience of working with a tool. Trials cannot see how well a harness recovers when you correct it mid task, how good its skill system gets once your team has invested in it, or whether the thing is pleasant to use at four in the afternoon. Those matter and I would not pick a tool on trials alone.

The results also rot fast. A scorecard is valid for the harness versions and model snapshots in its config digest and nothing else. Re-run it rather than citing it.

And the deeper problem is one I have not solved. Claude skills, Codex configs, and Cursor rules do not port between tools. I run multiple harnesses from a single window today and it works, but only because I translate between them by hand. A skill abstraction layer is the real missing piece here. The router is easy. The router is not the moat.

## Try it on your own repo

```bash
git clone https://github.com/<you>/harness-eval && cd harness-eval
pip install -e ".[dev]"
harness-eval doctor

harness-eval mine --repo ~/code/your-service --since "6 months ago"
harness-eval run --tasks tasks/mined.yaml --blast-radius high
```

Start with your high blast radius tasks. That is where the answer changes what you do on Monday, and it is the shortest path to finding out whether your current tool is the right one or just the one you started with.

If you run it, I would like to see the harness effect number. My guess is that it is larger than most teams expect and that almost nobody is currently measuring it. I would be glad to be wrong about the second part.

---

*Harness Eval is Apache 2.0. Issues and adapters welcome, especially for harnesses I have not wired up.*
