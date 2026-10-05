"""
Credential redaction for error records (C-ERR v2, design section 9).

Only free text (message, traceback_text) is redacted. Structured fields
are not secret by contract and are never touched. Callers must treat a
redaction failure as "write no free text", never as "write unredacted".
"""
import os
import re
from pathlib import Path
from typing import List, Tuple

# Environment variable names whose values are treated as secrets.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
# Shorter values collide with ordinary words and numbers.
_MIN_LEN = 6
_PATTERNS = [
    # Authorization header value up to end of line or quote
    (re.compile(r"(Authorization\s*[:=]\s*)[^\s'\",]+(\s+[^\s'\",]+)?", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1***"),
    # URL userinfo scheme://user:pass@host
    (re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1***@"),
    # query parameters carrying keys
    (re.compile(r"([?&](?:api_key|apikey|key|token|access_token)=)[^&\s'\"]+", re.I), r"\1***"),
]


def _alemsrc_secrets() -> List[Tuple[str, str]]:
    """Secret looking KEY=value pairs from ~/.alemsrc (missing file: none)."""
    path = Path.home() / ".alemsrc"
    if not path.exists():
        return []
    pairs = []
    for line in path.read_text(errors="replace").splitlines():
        name, sep, value = line.replace("export ", "", 1).partition("=")
        if sep and _SECRET_NAME.search(name):
            pairs.append((name.strip(), value.strip().strip("'\"")))
    return pairs


def secret_values() -> List[Tuple[str, str]]:
    """(name, value) of secrets known to this process, longest value first."""
    pairs = [(k, v) for k, v in os.environ.items() if _SECRET_NAME.search(k)]
    pairs += _alemsrc_secrets()
    # longest first so a value containing another is replaced whole
    pairs = [(k, v) for k, v in pairs if len(v) >= _MIN_LEN]
    return sorted(pairs, key=lambda kv: -len(kv[1]))


def redact(text: str) -> Tuple[str, int]:
    """
    Redact secrets from free text.

    Returns:
        (redacted text, number of replacements). Raises on internal failure;
        the caller then omits the text (design section 9).
    """
    if not text:
        return text, 0
    count = 0
    for name, value in secret_values():
        if value in text:
            count += text.count(value)
            text = text.replace(value, "***" + name)
    for pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        count += n
    return text, count
