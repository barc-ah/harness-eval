"""Concrete adapters, one per harness.

Flags drift between releases. Every adapter reads its argv template from
config/harnesses.yaml when one is present, and falls back to the defaults
below. When a harness changes its CLI, edit the YAML, not this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import HarnessAdapter, InvocationResult, Usage, scrape_usage


class ClaudeCodeAdapter(HarnessAdapter):
    """Anthropic Claude Code, headless via -p.

    Emits structured JSON usage with --output-format json, which makes it the
    only adapter here that reports tokens and cost without scraping.
    """

    name = "claude-code"
    binary = "claude"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        cmd = [
            self.binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def parse_usage(self, result: InvocationResult) -> Usage:
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return scrape_usage(result.stdout + result.stderr)
        usage = payload.get("usage") or {}
        return Usage(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=payload.get("total_cost_usd"),
            turns=payload.get("num_turns"),
        )


class CodexAdapter(HarnessAdapter):
    """OpenAI Codex CLI, non-interactive via exec.

    Consistently leaner on tokens than Claude Code for the same work, which
    is exactly the kind of tradeoff a single accuracy number hides.
    """

    name = "codex"
    binary = "codex"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        cmd = [
            self.binary,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
        ]
        if self.model:
            cmd += ["-m", self.model]
        return cmd + self.extra_args + [prompt]

    def parse_usage(self, result: InvocationResult) -> Usage:
        return scrape_usage(result.stdout + result.stderr)


class CursorAdapter(HarnessAdapter):
    """Cursor CLI agent, headless.

    Worth including even if nobody on the team uses the editor. Independent
    runs have put the same model several points higher inside Cursor's
    runtime than in its own vendor harness, so leaving it out biases the
    comparison toward whichever tool you already prefer.
    """

    name = "cursor"
    binary = "cursor-agent"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        cmd = [self.binary, "-p", prompt, "--output-format", "text", "--force"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def parse_usage(self, result: InvocationResult) -> Usage:
        return scrape_usage(result.stdout + result.stderr)


class AiderAdapter(HarnessAdapter):
    """Aider, the thin scaffold baseline.

    Include it as a control. If a heavyweight harness cannot beat a minimal
    scaffold on your tasks, the harness is not earning its token bill.
    """

    name = "aider"
    binary = "aider"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        cmd = [
            self.binary,
            "--message",
            prompt,
            "--yes-always",
            "--no-auto-commit",
            "--no-gitignore",
            "--no-check-update",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def parse_usage(self, result: InvocationResult) -> Usage:
        return scrape_usage(result.stdout + result.stderr)


class OpenCodeAdapter(HarnessAdapter):
    """OpenCode, open source, bring your own model.

    Useful for separating harness quality from vendor model access, since you
    can point it at the same model another adapter is running.
    """

    name = "opencode"
    binary = "opencode"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        cmd = [self.binary, "run", prompt]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def parse_usage(self, result: InvocationResult) -> Usage:
        return scrape_usage(result.stdout + result.stderr)


class ShellAdapter(HarnessAdapter):
    """Escape hatch for any harness not covered above.

    Give it a command template with {prompt} and {model} placeholders and it
    behaves like a first class adapter. New harnesses appear faster than this
    repo can track them, so wiring one should not require a pull request.

        - name: my-harness
          adapter: shell
          command: "mytool run --model {model} -- {prompt}"
    """

    name = "shell"
    binary = "sh"

    def __init__(self, command: str = "true", **kwargs) -> None:
        super().__init__(**kwargs)
        self.command = command

    def available(self) -> bool:
        head = self.command.split()[0] if self.command.split() else ""
        import shutil as _sh

        return bool(head) and (_sh.which(head) is not None or head in {"true", "sh"})

    def version(self) -> str | None:
        return "shell"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        import shlex

        rendered = self.command.format(
            prompt=shlex.quote(prompt),
            model=shlex.quote(self.model or ""),
            workdir=shlex.quote(str(workdir)),
        )
        return ["sh", "-c", rendered]

    def parse_usage(self, result: InvocationResult) -> Usage:
        return scrape_usage(result.stdout + result.stderr)


class NoopAdapter(HarnessAdapter):
    """Does nothing. Used by the test suite and by --dry-run.

    Also acts as the floor: any harness that cannot beat the no-op on a task
    means the task is broken, not that the harness is good.
    """

    name = "noop"
    binary = "true"

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        return ["true"]


REGISTRY: dict[str, type[HarnessAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    CodexAdapter.name: CodexAdapter,
    CursorAdapter.name: CursorAdapter,
    AiderAdapter.name: AiderAdapter,
    OpenCodeAdapter.name: OpenCodeAdapter,
    ShellAdapter.name: ShellAdapter,
    NoopAdapter.name: NoopAdapter,
}


def build_adapter(spec: dict) -> HarnessAdapter:
    """Instantiate an adapter from a config block."""
    spec = dict(spec)
    kind = spec.pop("adapter", spec.get("name"))
    label = spec.pop("name", kind)
    if kind not in REGISTRY:
        raise ValueError(
            f"unknown adapter {kind!r}. known: {sorted(REGISTRY)}"
        )
    spec.pop("enabled", None)
    adapter = REGISTRY[kind](**spec)
    adapter.name = label
    return adapter
