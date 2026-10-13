"""
================================================================================
ANSWER EXTRACTION HELPERS  —  core/execution/scorers/answer_extract.py
================================================================================

PURPOSE:
    Shared, pure functions used by ExactMatchScorer and NumericScorer.
    Ported from the frozen offline scorer v3 (paper data fix, Oct 13 2026)
    so live scoring and back scoring apply identical rules.

    normalize()              text normalization for answer matching
    contains_answer()        whole token containment, optional hedge guard
    extract_final_number()   final answer number from prose
    numbers_match()          tolerance from the expected value's precision,
                             percent form accepted for fractional answers
    auto_scorer_type()       scorer type from an expected answer string

FIXES (bugspec_scorers_live.md):
    BUG-1  exact match compared the whole output to a short answer
    BUG-2  numeric took the FIRST number in the output, not the final answer
    BUG-3  any digit in the expected answer selected the numeric scorer

AUTHOR: Deepak Panigrahy
================================================================================
"""

import re
import unicodedata
from fractions import Fraction
from typing import Optional, Sequence, Tuple

SCORER_LOGIC_VERSION = "v3"

_ARTICLES = {"a", "an", "the"}
_NUM_TOKEN = r"[-+]?\d+\s*/\s*\d+|[-+]?\$?\d[\d,]*(?:\.\d+)?%?|[-+]?\$?\.\d+%?"
_NUM_RE = re.compile(_NUM_TOKEN)


# ---------------------------------------------------------------------------
# Text matching
# ---------------------------------------------------------------------------
def normalize(text: Optional[str]) -> str:
    """NFKC, lowercase, punctuation to spaces (keeps decimals and minus signs
    inside numbers), collapse whitespace, drop a leading article."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"(?<![\w])-(?!\d)|(?<=\w)-(?=\D)", " ", s)
    s = re.sub(r"[^\w\s.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split(" ")
    if len(parts) > 1 and parts[0] in _ARTICLES:
        s = " ".join(parts[1:])
    return s


def _tokens(text: Optional[str]) -> list:
    n = normalize(text)
    return n.split(" ") if n else []


def _contains_seq(hay: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle:
        return False
    k = len(needle)
    return any(list(hay[i:i + k]) == list(needle) for i in range(len(hay) - k + 1))


def contains_answer(output: str, expected: str,
                    answer_set: Optional[Sequence[str]] = None) -> Tuple[bool, str]:
    """True if the normalized expected answer appears in the output as a whole
    token sequence. With a closed answer set, two or more options present
    counts as a hedge and fails. Returns (passed, rule)."""
    out_toks = _tokens(output)
    if not _contains_seq(out_toks, _tokens(expected)):
        return False, "token_contains"
    if answer_set:
        present = {normalize(a) for a in answer_set if _contains_seq(out_toks, _tokens(a))}
        if len(present) >= 2:
            return False, "hedge_guard"
    return True, "token_contains"


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
def parse_number(tok: Optional[str]) -> Optional[float]:
    if tok is None:
        return None
    t = str(tok).strip().replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    if not t:
        return None
    try:
        return float(Fraction(t)) if "/" in t else float(t)
    except (ValueError, ZeroDivisionError):
        return None


def _is_percent(text: str, tok: str, end: int) -> bool:
    return tok.rstrip().endswith("%") or bool(
        re.match(r"\s*(?:%|percent|per cent)", text[end:end + 12], re.I))


def extract_final_number(output: Optional[str]) -> Tuple[Optional[float], str, bool]:
    """Final answer number. Priority: GSM8K '#### n', 'answer is/: n',
    then the LAST number in the text. Returns (value, rule, stated_as_percent)."""
    if not output:
        return None, "empty", False
    text = unicodedata.normalize("NFKC", str(output))
    for pat, rule in ((r"####\s*(" + _NUM_TOKEN + ")", "gsm8k_marker"),
                      (r"answer\s*(?:is|:|=)\s*(?:approximately\s+|about\s+)?(" + _NUM_TOKEN + ")",
                       "answer_phrase")):
        ms = list(re.finditer(pat, text, re.I))
        if ms:
            m = ms[-1]
            return parse_number(m.group(1)), rule, _is_percent(text, m.group(1), m.end(1))
    ms = [m for m in _NUM_RE.finditer(text) if parse_number(m.group(0)) is not None]
    if ms:
        m = ms[-1]
        return parse_number(m.group(0)), "last_number", _is_percent(text, m.group(0), m.end(0))
    return None, "no_number", False


def numbers_match(x: float, expected_text: str, x_is_percent: bool = False,
                  rel_tol: float = 1e-6, abs_tol: float = 1e-9) -> Tuple[bool, float, str]:
    """Compare an extracted value with the expected answer.
    An expected value with d decimal places is a rounded value: accept anything
    within half a unit of its last place. Whole number answers stay exact.
    A percent stated for a fractional expected value (77.78% for 0.778) is
    accepted. Returns (passed, compared_value, rule)."""
    y = parse_number(expected_text)
    if y is None:
        return False, x, "expected_not_numeric"
    m = re.search(r"\.(\d+)", str(expected_text))
    prec = 0.5 * 10 ** (-len(m.group(1))) if m else 0.0
    if abs(x - y) <= max(abs_tol, rel_tol * abs(y), prec + 1e-12):
        return True, x, "direct"
    if x_is_percent and 0 < abs(y) < 1:
        xs = x / 100
        out_dec = re.search(r"\.(\d+)", repr(x))
        pct_prec = 0.5 * 10 ** (-(len(out_dec.group(1)) + 2)) if out_dec and x != int(x) else 0.005
        if abs(xs - y) <= max(prec, pct_prec) + 1e-12:
            return True, xs, "percent"
    return False, x, "mismatch"


# ---------------------------------------------------------------------------
# Scorer type detection
# ---------------------------------------------------------------------------
def auto_scorer_type(expected_answer: Optional[str]) -> str:
    """'numeric' when the WHOLE expected answer is a number, 'exact_match' for
    a non numeric answer of at most 5 tokens, otherwise 'none' (unscoreable
    without an explicit expectation block or an LLM judge)."""
    if expected_answer is None:
        return "none"
    e = str(expected_answer).strip()
    if not e:
        return "none"
    if re.fullmatch(_NUM_TOKEN, e.replace(" ", "")) and parse_number(e) is not None:
        return "numeric"
    if len(_tokens(e)) <= 5:
        return "exact_match"
    return "none"
