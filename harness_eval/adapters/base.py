"""Adapter interface.

A harness is not a model. It is the whole runtime around the model: how it
edits files, what it keeps in context, which tools it can reach, how it runs
tests, and what skills it carries. Published benchmarks collapse harness and
model into one number, which is why the same model can score 20+ points apart
depending only on which harness wraps it.

Each adapter here does one job: take a prompt and a working directory, run
the harness headlessly, and report back what it cost. Scoring happens in
oracle.py against the resulting diff, never inside the adapter.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class InvocationResult:
    exit_code: int
    stdout: str
    stderr: str
    wall_clock_sec: float
    timed_out: bool = False


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None


class HarnessAdapter(ABC):
    """Base class for every supported harness."""

    name: str = "abstract"
    binary: str = ""

    def __init__(
        self,
        model: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        binary: str | None = None,
    ) -> None:
        self.model = model
        self.extra_args = extra_args or []
        self.env_overrides = env or {}
        if binary:
            self.binary = binary

    # --- required per-harness detail -------------------------------------

    @abstractmethod
    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        """Return the argv for a single headless run."""

    def parse_usage(self, result: InvocationResult) -> Usage:
        """Pull token and cost numbers out of harness output where possible.

        Most harnesses do not print structured usage in headless mode, so the
        default returns nothing rather than guessing. A missing number is
        better than an invented one.
        """
        return Usage()

    # --- shared plumbing --------------------------------------------------

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def version(self) -> str | None:
        if not self.available():
            return None
        try:
            out = subprocess.run(
                [self.binary, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (out.stdout or out.stderr).strip().splitlines()[0][:120]
        except Exception:  # noqa: BLE001 - version probing must never abort a trial
            return None

    def build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.env_overrides)
        # Keep runs non-interactive and comparable.
        env.setdefault("CI", "1")
        env.setdefault("TERM", "dumb")
        env.setdefault("NO_COLOR", "1")
        return env

    def run(
        self, prompt: str, workdir: Path, timeout_sec: int = 1800
    ) -> InvocationResult:
        cmd = self.build_command(prompt, workdir)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=self.build_env(),
            )
            return InvocationResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                wall_clock_sec=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as exc:
            return InvocationResult(
                exit_code=124,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                wall_clock_sec=time.monotonic() - start,
                timed_out=True,
            )
        except FileNotFoundError:
            return InvocationResult(
                exit_code=127,
                stdout="",
                stderr=f"binary not found: {self.binary}",
                wall_clock_sec=time.monotonic() - start,
            )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "binary": self.binary,
            "model": self.model,
            "extra_args": self.extra_args,
            "version": self.version(),
            "available": self.available(),
        }


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


_TOKEN_PAT = re.compile(
    r"(?P<kind>input|output|prompt|completion)[ _-]?tokens?\D{0,12}?(?P<n>[\d,]+)",
    re.IGNORECASE,
)
_COST_PAT = re.compile(r"(?:cost|total)\D{0,12}?\$\s*([\d.]+)", re.IGNORECASE)


def scrape_usage(text: str) -> Usage:
    """Best effort usage scrape from unstructured harness output.

    Only used by adapters whose CLIs print a human readable summary. If the
    pattern does not match, the field stays None. Reports must be able to
    tell 'not measured' apart from 'zero'.
    """
    usage = Usage()
    for match in _TOKEN_PAT.finditer(text):
        n = int(match.group("n").replace(",", ""))
        kind = match.group("kind").lower()
        if kind in ("input", "prompt"):
            usage.input_tokens = n
        else:
            usage.output_tokens = n
    cost = _COST_PAT.search(text)
    if cost:
        try:
            usage.cost_usd = float(cost.group(1))
        except ValueError:
            pass
    return usage
