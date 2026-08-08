"""Config loading.

Harness CLIs change flags between releases. Keeping the argv extras in YAML
means a broken adapter is a config edit, not a code change and a release.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .adapters import HarnessAdapter, build_adapter
from .models import Task

DEFAULT_CONFIG = Path("config/harnesses.yaml")


def load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text()) or {}


def load_adapters(
    path: str | Path = DEFAULT_CONFIG, only: list[str] | None = None
) -> list[HarnessAdapter]:
    cfg = load_yaml(path)
    adapters: list[HarnessAdapter] = []
    for block in cfg.get("harnesses", []):
        if not block.get("enabled", True):
            continue
        if only and block.get("name") not in only:
            continue
        adapters.append(build_adapter(block))
    if not adapters:
        raise ValueError(f"no enabled harnesses in {path}")
    return adapters


def load_tasks(path: str | Path) -> list[Task]:
    """Load tasks from a file or a directory of task files."""
    p = Path(path)
    files = sorted(p.rglob("*.y*ml")) if p.is_dir() else [p]
    tasks: list[Task] = []
    for f in files:
        data = load_yaml(f)
        blocks = data.get("tasks", [data]) if isinstance(data, dict) else data
        for block in blocks:
            if not block or "id" not in block:
                continue
            tasks.append(Task.from_dict(block))
    return tasks


def save_tasks(tasks: list[Task], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(
            {"tasks": [t.to_dict() for t in tasks]},
            sort_keys=False,
            width=100,
        )
    )
    return out
