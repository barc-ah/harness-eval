"""Core data models for Harness Eval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class BlastRadius(str, Enum):
    """How much breaks if the change is wrong, and how hard it is to undo.

    This is the trigger for whether a task is worth running across multiple
    harnesses. Low blast radius work is cheap to revert, so a single harness
    is fine. High blast radius work spreads on failure and costs days.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def default_runs(self) -> int:
        return {"low": 1, "medium": 3, "high": 5}[self.value]

    @property
    def weight(self) -> float:
        """Weight applied when aggregating scores across a task set."""
        return {"low": 1.0, "medium": 2.0, "high": 4.0}[self.value]


class Outcome(str, Enum):
    RESOLVED = "resolved"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class Task:
    """A single benchmark task.

    Tasks come from two places: bundled samples under tasks/samples, and
    tasks mined from a repository's own git history. Mined tasks are far
    more honest than public benchmarks because they are the work your team
    actually does.
    """

    id: str
    prompt: str
    repo: str
    base_commit: str
    verify: list[str]
    blast_radius: BlastRadius = BlastRadius.MEDIUM
    setup: list[str] = field(default_factory=list)
    files_hint: list[str] = field(default_factory=list)
    timeout_sec: int = 1800
    reference_diff: str | None = None
    source: str = "sample"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        data = dict(data)
        br = data.pop("blast_radius", "medium")
        return cls(blast_radius=BlastRadius(br), **data)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["blast_radius"] = self.blast_radius.value
        return d

    @property
    def runs_needed(self) -> int:
        return self.blast_radius.default_runs


@dataclass
class OracleResult:
    """Verifiable signals. Not an LLM judgement.

    LLM-as-judge drifts badly on code. Everything here is either a process
    exit code or a number counted off the diff.
    """

    verify_passed: bool = False
    verify_exit_codes: list[int] = field(default_factory=list)
    verify_stdout_tail: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    files_touched: int = 0
    files_outside_hint: int = 0
    diff_empty: bool = True

    @property
    def churn(self) -> int:
        return self.lines_added + self.lines_removed


@dataclass
class RunResult:
    """One harness, one task, one attempt."""

    task_id: str
    harness: str
    model: str | None
    run_index: int
    outcome: Outcome
    oracle: OracleResult
    wall_clock_sec: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None
    interventions: int = 0
    exit_code: int | None = None
    error: str | None = None
    workspace: str | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def resolved(self) -> bool:
        return self.outcome == Outcome.RESOLVED

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["resolved"] = self.resolved
        d["total_tokens"] = self.total_tokens
        return d


@dataclass
class TrialReport:
    """The full output of a trial: every run, plus the config that produced it."""

    trial_id: str
    started_at: str
    finished_at: str | None
    config_digest: str
    runs: list[RunResult] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "config_digest": self.config_digest,
            "tasks": [t.to_dict() for t in self.tasks],
            "runs": [r.to_dict() for r in self.runs],
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text())
