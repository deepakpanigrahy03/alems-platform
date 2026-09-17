"""
JUDGMENT ENGINE  —  core/execution/judgment_engine.py
================================================================================

PURPOSE:
    Compute-only N-judge quality scoring, wrapping the REAL live scorer
    path (TaskExpectationAdapter -> ScorerRegistry), not the dead
    quality_judge.py path this session discovered was never actually
    called in production (zero call sites for QualityJudge.judge()).

    N-judge reconciliation (median bands) is ported from quality_judge.py
    verbatim — that logic was correct and tested, it was just never
    wired to anything live. This is the first time n_judges/dual_judge
    from task_quality_config actually take effect.

    NEVER writes to the database. Returns a JudgmentComputation; the
    caller (experiment_runner.py) attaches it to PostRunPayload;
    OutputQualityExtension.on_post_run() is the only thing that writes
    output_quality/output_quality_judges — this is the INV-3 fix: today
    experiment_runner.py hand-writes that SQL directly, bypassing the
    extension system entirely.

    ASSUMPTION (stated, not verified this session — confirm before
    relying on it): TaskExpectationAdapter.score() reads task_category's
    n_judges via its own config lookup path, separate from
    task_quality_config directly. This engine reads n_judges from
    task_quality_config itself (same table, same column, matching what
    quality_judge.py always did), so if TaskExpectationAdapter has a
    DIFFERENT config source for judge count, the two could disagree.
    Verify scripts/... wherever TaskExpectationAdapter's config
    resolution actually lives before this ships.

AUTHOR: Deepak Panigrahy (original 8.5C reconciliation logic),
        redesigned for SPEC 35J against the real live path.
================================================================================
"""

import hashlib
import json
import logging
import statistics
from typing import List, Optional, Tuple

from core.execution.expectation.schema import Expectation
from core.execution.expectation.adapter import TaskExpectationAdapter
from core.execution.judgment_types import JudgmentComputation, JudgmentResult

logger = logging.getLogger(__name__)

_adapter = TaskExpectationAdapter()

ACCEPTANCE_THRESHOLD = 0.7
MEDIAN_TIGHT_BAND = 0.20
MEDIAN_LOOSE_BAND = 0.40


def judge(
    conn,
    task_category: str,
    expectation: Expectation,
    model_output: str,
    agentic_result: Optional[dict] = None,
    energy_uj: Optional[int] = None,
    task_id_value: Optional[str] = None,
) -> JudgmentComputation:
    """
    Score one attempt using N judges (from task_quality_config.n_judges),
    each call going through the real live path: TaskExpectationAdapter.score().

    Compute only — conn is used ONLY to read task_quality_config for
    n_judges/threshold, matching the original quality_judge.py's
    _load_config() read pattern. No writes.

    Args:
        conn          : sqlite3 connection, read-only use.
        task_category : task category string, keys task_quality_config.
        expectation   : parsed Expectation (what TaskExpectationAdapter needs).
        model_output  : the LLM's actual text response.
        agentic_result: full agentic result dict, for structural scoring.
        energy_uj     : energy_uj of the inference run, carried for context.

    Returns:
        JudgmentComputation. If no task_quality_config row exists for
        task_category, n_judges defaults to 1 (single call, matching
        today's real behavior) rather than skipping entirely — this
        differs from quality_judge.py's original "skip if no config"
        because TaskExpectationAdapter itself already has its own
        is_scoreable() skip logic (expectation.is_scoreable()), so a
        missing task_quality_config row is not automatically "nothing
        to score" the way it was in the old dead code path.
    """
    n_judges, threshold, judge_model_set = _load_judge_config(conn, task_category)

    raw_scores: List[Tuple[float, float, str]] = []
    models_used: List = []
    expected_value_seen = ""
    for i in range(n_judges):
        # SPEC 35J: round-robin across configured judge models. Confirmed
        # this was NEVER wired before this fix — judge_model was always
        # omitted, silently falling through to LLMJudgeScorer's hardcoded
        # default (llama3.1:8b via Ollama, not installed on gn100),
        # causing every real llm_judge call to fail with parse_failed.
        model = judge_model_set[i % len(judge_model_set)] if judge_model_set else None
        try:
            result = _adapter.score(
                expectation=expectation,
                model_output=model_output,
                agentic_result=agentic_result,
                judge_model=model,
            )
            raw_scores.append((result.score, result.confidence, result.reasoning))
            models_used.append(model)
            if not expected_value_seen and getattr(result, "expected_value", ""):
                expected_value_seen = result.expected_value
        except Exception as exc:
            logger.error("judgment_engine.judge: adapter.score raised for judge %d (model=%s): %s", i, model, exc)
            raw_scores.append((0.0, 0.0, "scorer_failed"))
            models_used.append(model)

    valid_scores = [(s, c, r) for s, c, r in raw_scores if c > 0.0]
    n_valid = len(valid_scores)

    normalized_score, score_method = _reconcile(
        [s for s, c, r in valid_scores], n_judges
    )

    if normalized_score is None:
        pass_fail = None
    else:
        pass_fail = 1 if normalized_score >= threshold else 0

    raw_score = statistics.mean([s for s, c, r in valid_scores]) if valid_scores else None

    per_judge: List[Tuple[Optional[str], Optional[str], float, float, str]] = [
        (
            (models_used[i].get("model_id") if isinstance(models_used[i], dict) else models_used[i])
            or (expectation.scorer_type if hasattr(expectation, "scorer_type") else "unknown"),
            (models_used[i].get("provider") if isinstance(models_used[i], dict) else None),
            score, confidence, reasoning,
        )
        for i, (score, confidence, reasoning) in enumerate(raw_scores)
    ]

    logger.debug(
        "judgment_engine.judge: task_category=%s n_judges=%d score=%s method=%s n_valid=%d",
        task_category, n_judges,
        normalized_score if normalized_score is not None else "None",
        score_method, n_valid,
    )

    # scorer_version: stable identity string — scorer_type from the expectation
    # plus the adapter class name. No registry lookup needed; scorer_type is
    # the canonical registered name and is already resolved by this point.
    _scorer_version = (
        f"{expectation.scorer_type}:{_adapter.__class__.__name__}"
        if expectation.scorer_type else "unknown"
    )

    # scorer_config_hash: SHA-256 (first 16 hex chars) of the exact
    # task_quality_config row used for this call. Fingerprints category +
    # judge_method + n_judges + judge_model_set so any researcher can
    # reproduce the exact scoring setup for any output_quality row.
    _config_payload = json.dumps({
        "task_category":   task_category,
        "judge_method":    expectation.scorer_type,
        "n_judges":        n_judges,
        "judge_model_set": judge_model_set,
    }, sort_keys=True)
    _scorer_config_hash = hashlib.sha256(_config_payload.encode()).hexdigest()[:16]

    return JudgmentComputation(
        result=JudgmentResult(
            normalized_score=normalized_score,
            pass_fail=pass_fail,
            score_method=score_method,
            n_judges_used=n_valid,
        ),
        task_id=task_id_value,
        task_category=task_category,
        metric_type="scalar",  # ASSUMPTION: TaskExpectationAdapter does not
                                # expose metric_type directly; using the most
                                # common value seen in production data this
                                # session. Verify against expectation.scorer_type
                                # if a non-scalar metric_type matters downstream.
        judge_method=expectation.scorer_type if hasattr(expectation, "scorer_type") else "unknown",
        raw_score=raw_score,
        per_judge=per_judge,
        energy_uj_at_judgment=energy_uj,
        scorer_version=_scorer_version,
        scorer_config_hash=_scorer_config_hash,
        expected_output=expected_value_seen or None,  # SPEC 35J Bug 2 fixed:
                                # now populated from ScoreResult.expected_value.
        actual_output=model_output,
    )


def _reconcile(scores: List[float], n_judges_requested: int) -> Tuple[Optional[float], str]:
    """Median-based reconciliation. Ported verbatim from quality_judge.py."""
    if not scores:
        return (None, "needs_review")
    if len(scores) == 1:
        return (scores[0], "single_judge")

    med = statistics.median(scores)
    deviations = [abs(s - med) for s in scores]

    if all(d <= MEDIAN_TIGHT_BAND for d in deviations):
        return (statistics.mean(scores), "averaged")
    if all(d <= MEDIAN_LOOSE_BAND for d in deviations):
        return (med, "consensus_median")

    outliers = [i for i, d in enumerate(deviations) if d > MEDIAN_TIGHT_BAND]
    if len(outliers) == 1:
        agreeing = [s for i, s in enumerate(scores) if i not in outliers]
        return (statistics.median(agreeing), "majority_median")

    return (None, "needs_review")


def _load_judge_count_and_threshold(conn, task_category: str) -> Tuple[int, float]:
    """
    Read n_judges/threshold from task_quality_config.

    Defaults to (1, ACCEPTANCE_THRESHOLD) if no row exists — matching
    today's real behavior (one call), rather than skipping, since
    TaskExpectationAdapter has its own independent skip logic.
    """
    try:
        row = conn.execute(
            "SELECT n_judges, threshold FROM task_quality_config WHERE task_category = ? LIMIT 1",
            (task_category,),
        ).fetchone()
        if row is None:
            return (1, ACCEPTANCE_THRESHOLD)
        return (row[0] or 1, row[1] or ACCEPTANCE_THRESHOLD)
    except Exception as exc:
        logger.error("judgment_engine._load_judge_count_and_threshold failed: %s", exc)
        return (1, ACCEPTANCE_THRESHOLD)

def _load_judge_config(conn, task_category: str) -> Tuple[int, float, list]:
    """
    Read n_judges/threshold/judge_model_set from task_quality_config.

    judge_model_set: JSON array of {"provider":..., "model_id":...} dicts
    (seeded via s012/s013). Empty list if NULL, malformed, or no config
    row — callers fall back to the scorer's own default model in that
    case (which, on this platform, tries Ollama and fails — configure
    judge_model_set for every llm_judge/semantic category).

    Defaults to (1, ACCEPTANCE_THRESHOLD, []) if no row exists.
    """
    import json
    try:
        row = conn.execute(
            "SELECT n_judges, threshold, judge_model_set FROM task_quality_config WHERE task_category = ? LIMIT 1",
            (task_category,),
        ).fetchone()
        if row is None:
            return (1, ACCEPTANCE_THRESHOLD, [])
        n_judges = row[0] or 1
        threshold = row[1] or ACCEPTANCE_THRESHOLD
        model_set = []
        if row[2]:
            try:
                parsed = json.loads(row[2])
                if isinstance(parsed, list):
                    model_set = parsed
            except Exception:
                logger.warning("judgment_engine: malformed judge_model_set for category=%s: %r", task_category, row[2])
        return (n_judges, threshold, model_set)
    except Exception as exc:
        logger.error("judgment_engine._load_judge_config failed: %s", exc)
        return (1, ACCEPTANCE_THRESHOLD, [])