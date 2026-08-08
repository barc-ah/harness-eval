#!/usr/bin/env bash
# Builds the throwaway repo that tasks/samples/starter.yaml runs against.
#
# The point is calibration, not benchmarking. Run this once, run a trial, and
# confirm the plumbing works end to end before you point the tool at a
# repository where the numbers matter.
set -euo pipefail

DEST="${1:-/tmp/harness-eval-fixture}"
rm -rf "$DEST"
mkdir -p "$DEST/app" "$DEST/tests"
cd "$DEST"

cat > app/__init__.py <<'PY'
PY

cat > app/text_utils.py <<'PY'
"""String helpers. slugify is missing on purpose."""


def titleize(text: str) -> str:
    return " ".join(word.capitalize() for word in text.split())
PY

cat > tests/test_text_utils.py <<'PY'
import pytest

from app.text_utils import slugify


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello World", "hello-world"),
        ("  Trailing spaces  ", "trailing-spaces"),
        ("Multiple---Hyphens", "multiple-hyphens"),
        ("Symbols!@#$%here", "symbols-here"),
        ("MiXeD CaSe 123", "mixed-case-123"),
        ("---", ""),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected
PY

cat > app/client.py <<'PY'
"""HTTP client with a naive retry loop."""

import time


class TransientError(Exception):
    pass


def call_with_retry(fn, attempts: int = 3, sleep=time.sleep):
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            sleep(1.0)
    raise last
PY

cat > tests/test_client.py <<'PY'
import pytest

from app.client import TransientError, call_with_retry


def test_succeeds_first_try():
    assert call_with_retry(lambda: 42, sleep=lambda _: None) == 42


def test_backoff_grows_and_is_capped():
    delays = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise TransientError("nope")

    with pytest.raises(TransientError):
        call_with_retry(flaky, attempts=6, sleep=delays.append)

    assert len(delays) >= 4
    assert max(delays) <= 30.0
    # Jitter means delays are not monotonic, but the ceiling must grow.
    assert max(delays[3:]) > max(delays[:2])


def test_default_attempts_is_five():
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise TransientError("nope")

    with pytest.raises(TransientError):
        call_with_retry(always_fails, sleep=lambda _: None)
    assert calls["n"] == 5
PY

cat > app/orders.py <<'PY'
"""Order storage. Amounts are floats, which is the bug."""

from dataclasses import dataclass


@dataclass
class Order:
    id: str
    amount: float
    currency: str = "USD"

    def to_row(self) -> dict:
        return {"id": self.id, "amount": self.amount, "currency": self.currency}

    @classmethod
    def from_row(cls, row: dict) -> "Order":
        return cls(id=row["id"], amount=row["amount"], currency=row.get("currency", "USD"))
PY

mkdir -p app/migrations
cat > app/migrations/__init__.py <<'PY'
PY

cat > tests/test_orders.py <<'PY'
from app.orders import Order


def test_amount_is_minor_units_integer():
    o = Order(id="a1", amount_minor=1999)
    assert isinstance(o.amount_minor, int)
    assert o.to_row()["amount_minor"] == 1999


def test_no_float_drift_on_roundtrip():
    total = 0
    for _ in range(1000):
        total += Order(id="x", amount_minor=1).amount_minor
    assert total == 1000
PY

cat > tests/test_migration_roundtrip.py <<'PY'
from app.migrations import upgrade_amount, downgrade_amount


def test_upgrade_converts_float_to_minor_units():
    assert upgrade_amount(19.99) == 1999
    assert upgrade_amount(0.01) == 1
    assert upgrade_amount(0.0) == 0


def test_downgrade_is_lossless_for_two_decimals():
    for value in (19.99, 0.01, 123.45, 0.0):
        assert downgrade_amount(upgrade_amount(value)) == value
PY

cat > pyproject.toml <<'PY'
[project]
name = "fixture-app"
version = "0.0.1"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]
PY

git init -q
git config user.email "fixture@example.com"
git config user.name "fixture"
git add -A
git commit -q -m "initial fixture"

echo "fixture repo at $DEST"
echo "three tasks are unsolved by design: slugify, backoff, minor units"
