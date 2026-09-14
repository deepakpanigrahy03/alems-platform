"""
================================================================================
LLM JUDGE SCORER  —  core/execution/scorers/llm_judge.py
================================================================================

PURPOSE:
    LLM-as-judge scorer. Calls a configurable judge model to score the
    actual output against the expected answer. Returns a float score,
    confidence, and reasoning extracted from the model response.

    This scorer runs AFTER core energy_uj is committed (called from
    QualityJudge which is called from experiment_runner after save_pair/
    save_single). The judge call's own energy does not contaminate the
    inference measurement — this is the Observer Energy separation.

    Never raises. Returns (0.0, 0.0, 'scorer_failed') on any error.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import json
import logging
import os
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC

logger = logging.getLogger(__name__)

# Default judge model — used when judge_model is None.
# Overridden per task category via task_quality_config.judge_model_set.
_DEFAULT_JUDGE_MODEL = "llama3.1:8b"

# Temperature for judge calls — 0.0 for deterministic scoring.
_JUDGE_TEMPERATURE = 0.0

# Prompt template for LLM judge.
# {actual}, {expected}, {rubric_section} are filled at call time.
_JUDGE_PROMPT_TEMPLATE = """You are a quality judge evaluating an AI assistant's response.

Task: Score the following response on a scale from 0.0 to 1.0.

Expected answer (reference):
{expected}

Actual response to evaluate:
{actual}

{rubric_section}

Scoring criteria:
- 1.0: Perfect match or fully correct answer
- 0.8: Mostly correct with minor errors or omissions
- 0.6: Partially correct, key concepts present but incomplete
- 0.4: Some relevant content but significant errors
- 0.2: Minimal relevance, mostly incorrect
- 0.0: Completely wrong or irrelevant

Respond in JSON format ONLY:
{{
  "score": <float 0.0-1.0>,
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence explanation>"
}}"""

_RUBRIC_SECTION_TEMPLATE = """Scoring rubric:
{rubric}

"""


class LLMJudgeScorer(ScorerABC):
    """
    LLM-as-judge quality scorer.

    Calls an LLM to evaluate the actual output against the expected answer.
    Parses the model response as JSON to extract score, confidence, and reasoning.
    Falls back to score=0.0, confidence=0.0 on any API or parse error.

    The judge model is specified per call via judge_model parameter.
    When None, uses _DEFAULT_JUDGE_MODEL.

    Use for: reasoning, planning, summarization, research, creative writing —
    tasks where the answer requires semantic judgment, not exact matching.

    Confidence: model-reported (from JSON response), or 0.0 on failure.
    """

    SCORER_TYPE = "llm_judge"
    METRIC_TYPES = ("scalar", "pairwise")

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score using an LLM judge model.

        Builds a prompt with actual output, expected answer, and optional rubric.
        Calls the judge model via the platform's OpenAI-compatible adapter.
        Parses JSON response for score, confidence, reasoning.

        Args:
            actual     : LLM's actual output for this attempt.
            expected   : Reference answer or rubric key.
            judge_model: Model identifier (e.g. "llama3.1:8b", "gpt-4o").
                         Uses _DEFAULT_JUDGE_MODEL when None.
            rubric     : Optional dict with scoring criteria for scalar tasks.
                         Serialized to string and included in prompt.

        Returns:
            (score, confidence, reasoning) from model response.
            (0.0, 0.0, "scorer_failed") on any error.
        """
        try:
            model = judge_model or _DEFAULT_JUDGE_MODEL
            prompt = self._build_prompt(actual, expected, rubric)
            response_text = self._call_judge(prompt, model)
            return self._parse_response(response_text)
        except Exception as exc:
            logger.error("LLMJudgeScorer.score failed: %s", exc)
            return (0.0, 0.0, "scorer_failed")

    def _build_prompt(
        self,
        actual: str,
        expected: str,
        rubric: Optional[dict],
    ) -> str:
        """Build the judge prompt with all context filled in."""
        rubric_section = ""
        if rubric:
            rubric_str = json.dumps(rubric, indent=2)
            rubric_section = _RUBRIC_SECTION_TEMPLATE.format(rubric=rubric_str)

        return _JUDGE_PROMPT_TEMPLATE.format(
            actual=actual or "(empty response)",
            expected=expected or "(no reference answer)",
            rubric_section=rubric_section,
        )

    def _call_judge(self, prompt: str, model: str) -> str:
        """
        Call the judge model via the platform's LLM adapter.

        Uses the openai_compat adapter (VLLM remote or local Ollama)
        to avoid introducing a new API dependency. The adapter is
        instantiated directly rather than going through the full harness
        to keep the scorer stateless and fast.

        Returns the raw response text from the model.
        Raises on API error — caller handles with (0.0, 0.0, 'scorer_failed').
        """
        try:
            # Import here to avoid circular imports at module level.
            # The scorer package must not import experiment_runner or harness.
            import requests

            # Use VLLM remote if configured, otherwise fall back to local.
            vllm_url = os.environ.get("ALEMS_VLLM_REMOTE_URL", "")
            if vllm_url:
                base_url = vllm_url
            else:
                base_url = "http://localhost:11434/v1"

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": _JUDGE_TEMPERATURE,
                "max_tokens": 256,
            }
            resp = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except Exception as exc:
            logger.warning("LLMJudgeScorer._call_judge failed for model=%s: %s", model, exc)
            raise

    def _parse_response(self, response_text: str) -> Tuple[float, float, str]:
        """
        Parse JSON response from judge model.

        Expects: {"score": float, "confidence": float, "reasoning": str}
        Clips score and confidence to [0.0, 1.0].
        Falls back to (0.0, 0.0, 'parse_failed') if JSON is malformed.
        """
        try:
            # Strip markdown code fences if model adds them.
            text = response_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                # Remove first and last fence lines.
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            data = json.loads(text)
            score = float(data.get("score", 0.0))
            confidence = float(data.get("confidence", 0.5))
            reasoning = str(data.get("reasoning", "no reasoning provided"))

            # Clip to valid range.
            score = max(0.0, min(1.0, score))
            confidence = max(0.0, min(1.0, confidence))

            return (score, confidence, reasoning)

        except Exception as exc:
            logger.warning(
                "LLMJudgeScorer._parse_response failed: %s — raw: %.100s",
                exc,
                response_text,
            )
            return (0.0, 0.0, f"parse_failed: {exc}")
