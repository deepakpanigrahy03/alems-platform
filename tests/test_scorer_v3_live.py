"""
tests/test_scorer_v3_live.py  —  live scorer fixes BUG-1, BUG-2, BUG-3.
Run: python -m pytest -q tests/test_scorer_v3_live.py
"""
import pytest

from core.execution.scorers.exact_match import ExactMatchScorer
from core.execution.scorers.numeric import NumericScorer
from core.execution.scorers.answer_extract import auto_scorer_type

EM = ExactMatchScorer()
NUM = NumericScorer()


def em(a, e, rubric=None):
    return EM.score(a, e, rubric=rubric)[0]


def num(a, e):
    return NUM.score(a, e)[0]


# ---- BUG-1: real rows from the prod store that scored 0 ----
def test_bug1_db_rows():
    assert em("The first president of the United States was George Washington. He served two terms.",
              "George Washington") == 1.0
    assert em("The chemical symbol for water is H2O. This symbol represents...", "H2O") == 1.0


def test_bug1_no_substring_matches():
    assert em("The answer is 120.", "12") == 0.0
    assert em("cattle are animals", "cat") == 0.0


def test_bug1_hedge_guard():
    assert em("It could be 12 or 14.", "12", rubric={"answer_set": ["12", "14"]}) == 0.0
    assert em("It is 12.", "12", rubric={"answer_set": ["12", "14"]}) == 1.0


def test_bug1_case_article_punctuation():
    assert em("Paris is the capital.", "paris") == 1.0
    assert em("It is the Eiffel Tower!", "The Eiffel Tower") == 1.0


# ---- BUG-2: final answer, not first number ----
def test_bug2_db_rows():
    assert num("John initially has 5 apples. When he buys 7 more, he now has 5 + 7 = 12 apples. "
               "So, John has 12 apples.", "12") == 1.0
    assert num("The overhead fraction is 0.7777777777777778.", "0.778") == 1.0


def test_bug2_markers_and_formats():
    assert num("first 5 then 6\n#### 72", "72") == 1.0
    assert num("The answer is $1,250.", "1250") == 1.0
    assert num("The answer is 42. Check: 6 times 7 is 42, not 41.", "42") == 1.0
    assert num("The probability is 1/4", "0.25") == 1.0


def test_bug2_percent_kept_from_sep16_fix():
    assert num("The overhead fraction is approximately 77.78%.", "0.778") == 1.0
    assert num("That is about 77.8 percent of the total.", "0.778") == 1.0
    assert num("The overhead is 7.78%.", "0.778") == 0.0


def test_bug2_strict_integers_and_no_1pct_slack():
    assert num("So John has 13 apples.", "12") == 0.0
    assert num("John has 12.4 apples", "12") == 0.0
    assert num("The distance is 258 miles.", "260") == 0.0   # old 1% tolerance passed this


def test_bug2_word_number_fallback():
    assert num("The answer is twelve.", "12") == 1.0


def test_no_number_is_fail_not_crash():
    s, c, r = NUM.score("I cannot determine that.", "12")
    assert s == 0.0 and "no number" in r


# ---- BUG-3: scorer type detection ----
@pytest.mark.parametrize("expected,kind", [
    ("260", "numeric"), ("$1,250", "numeric"), ("0.778", "numeric"), ("-3.5", "numeric"),
    ("George Washington", "exact_match"), ("H2O", "exact_match"), ("positive", "exact_match"),
    ("3-sentence summary covering planning overhead, tool selection, and computation fraction", "none"),
    ("A detailed explanation of why ice melts in the sun", "none"),
    (None, "none"), ("", "none"),
])
def test_bug3_auto_scorer_type(expected, kind):
    assert auto_scorer_type(expected) == kind


def test_reasoning_carries_logic_version():
    assert "v3" in EM.score("x", "y")[2]
    assert "v3" in NUM.score("12", "12")[2]
