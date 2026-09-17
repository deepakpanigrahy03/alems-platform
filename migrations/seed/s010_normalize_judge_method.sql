-- migrations/seed/s010_normalize_judge_method.sql
-- SPEC 35J Fix 2 (Problem 2): task_quality_config used 'semantic_similarity',
-- ScorerRegistry and output_quality use 'semantic'. Normalize to the
-- registry's spelling so scorer_registry.get(judge_method) resolves.
--
-- Per COMPLIANCE.md MSC-4: data-only, no DDL. Per SC-7: data-only
-- migrations do NOT get a schema_version entry.
-- Run AFTER v094_goal_output_scoring.sql (needs the CHECK constraint
-- already relaxed, or this UPDATE may fail against the old CHECK).

UPDATE task_quality_config
SET judge_method = 'semantic'
WHERE judge_method = 'semantic_similarity';
