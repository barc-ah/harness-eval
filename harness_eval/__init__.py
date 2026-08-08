"""Harness Eval: controlled comparison of AI coding harnesses."""

__version__ = "0.1.0"

from .models import BlastRadius, Outcome, RunResult, Task, TrialReport
from .runner import Runner
from .scoring import HarnessScore, harness_effect, pass_at_k, score_all

__all__ = [
    "BlastRadius",
    "HarnessScore",
    "Outcome",
    "RunResult",
    "Runner",
    "Task",
    "TrialReport",
    "__version__",
    "harness_effect",
    "pass_at_k",
    "score_all",
]
