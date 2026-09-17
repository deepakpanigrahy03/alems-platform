"""
JUDGMENT TYPES  —  core/execution/judgment_types.py
================================================================================

PURPOSE:
    Shared core types for scoring. Lives in core because core consumers
    (hallucination_detector.py, goal_tracker.py) import these directly —
    core never imports from extensions.

    Extracted from quality_judge.py (commit 16e9836) as part of SPEC 35J's
    compute/persist split.

AUTHOR: Deepak Panigrahy (extraction), original authorship 8.5C
================================================================================
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class JudgmentResult:
    """
    Lightweight result consumed synchronously by core (hallucination_detector,
    goal_tracker) right after judging. Unchanged in shape from the original
    quality_judge.py version, except quality_id is now Optional: the
    judgment_engine that produces this no longer writes to the DB, so no
    row exists yet at the moment this is constructed. The extension fills
    quality_id in after it persists, if a caller needs it post-write.

    normalized_score: float [0.0, 1.0] or None when needs_review.
    pass_fail       : 1 (pass) or 0 (fail) or None when needs_review.
    score_method    : how scores were reconciled across N judges. One of:
                      single_judge, averaged, consensus_median,
                      majority_median, needs_review, back_scored,
                      stub_skipped.
    quality_id      : output_quality row id, filled in after persistence.
                      None at compute time (judgment_engine never writes).
    n_judges_used   : number of judges that returned valid scores.
    """

    normalized_score: Optional[float]
    pass_fail: Optional[int]
    score_method: str
    quality_id: Optional[int] = None
    n_judges_used: int = 0


@dataclass
class JudgmentComputation:
    """
    Full computation record. judgment_engine.judge() returns this.
    Carries everything OutputQualityExtension needs to persist both
    output_quality and output_quality_judges rows, without the extension
    ever having to recompute or re-derive anything — it only writes what's
    already here.

    result          : the JudgmentResult (see above), for core consumers.
    task_category   : category used to load config, for the output_quality row.
    metric_type     : from task_quality_config, for the output_quality row.
    judge_method    : from task_quality_config, for the output_quality row.
    raw_score       : mean of valid per-judge scores, before reconciliation.
    per_judge       : one entry per judge call: (model, score, confidence, reasoning).
                      Includes failed judges (confidence=0.0), matching the
                      original quality_judge.py behavior of recording all N
                      attempts, not just the valid ones.
    energy_uj_at_judgment : energy_uj of the inference run, for context.
    expected_output, actual_output : the text that was judged.
    """

    result: JudgmentResult
    task_id: Optional[str]
    task_category: Optional[str]
    metric_type: str
    judge_method: str
    raw_score: Optional[float]
    per_judge: List[Tuple[Optional[str], float, float, str]] = field(default_factory=list)
    energy_uj_at_judgment: Optional[int] = None
    scorer_version: Optional[str] = None
    scorer_config_hash: Optional[str] = None
    expected_output: Optional[str] = None
    actual_output: Optional[str] = None
