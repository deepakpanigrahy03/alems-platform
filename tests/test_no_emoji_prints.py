"""
Guard (39.5.2e acceptance): no print call in core carries emoji or a DEBUG tag.

Scans the AST, so comments and docstrings never count. User output goes
through the console renderer, operational output through logging.
"""

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
# Emoji and pictographs; plain symbols such as ± and ° stay allowed.
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B55]")


def _string_parts(node):
    """All literal string fragments inside a call's arguments."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def test_no_emoji_or_debug_prints_in_core():
    offenders = []
    for path in sorted(CORE.rglob("*.py")):
        if "/tests/" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                text = "".join(_string_parts(node))
                if EMOJI.search(text) or "DEBUG" in text:
                    offenders.append("%s:%d" % (path.relative_to(CORE.parent), node.lineno))
    assert not offenders, "emoji or DEBUG prints in core:\n" + "\n".join(offenders)
