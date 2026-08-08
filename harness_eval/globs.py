"""Path matching.

`fnmatch` does not understand `**`, so `**/test_*.py` silently fails to match
a top level `test_app.py`. That bug is quiet and expensive here: it would let
a harness edit the graded tests without tripping the tamper guard, and every
score after that is fiction. Hence one helper, used everywhere.
"""

from __future__ import annotations

import fnmatch
from posixpath import normpath


def _normalise(path: str) -> str:
    return normpath(path.replace("\\", "/")).lstrip("./")


def path_matches(path: str, pattern: str) -> bool:
    """True if `path` matches a glob that may contain `**`."""
    p = _normalise(path)
    pat = pattern.replace("\\", "/")

    if fnmatch.fnmatch(p, pat):
        return True

    # `**/x` should also match `x` sitting at the root.
    if pat.startswith("**/") and fnmatch.fnmatch(p, pat[3:]):
        return True

    # `a/**` should match `a/b/c` as well as `a/b`.
    if pat.endswith("/**"):
        prefix = pat[:-3]
        if p == prefix or p.startswith(prefix.rstrip("/") + "/"):
            return True

    # `**/dir/**` should match any path containing that directory.
    if "/**" in pat and pat.startswith("**/"):
        middle = pat[3:].split("/**")[0]
        if f"/{middle}/" in f"/{p}/":
            return True

    return False


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(path_matches(path, pat) for pat in patterns)
