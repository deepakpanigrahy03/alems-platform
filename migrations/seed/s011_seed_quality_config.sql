-- migrations/seed/s011_seed_quality_config.sql
-- Replaces scripts/seed_quality_config.py's QUALITY_CONFIG list with a
-- tracked, checksummed seed file so `alems migrate` applies it
-- automatically on every fleet machine, the same as any other seed.
-- The old script becomes redundant after this runs once everywhere
-- (INSERT OR IGNORE means running both is harmless, but this file is
-- now the source of truth going forward).
--
-- 17 rows, one per task category. judge_method uses the registry's
-- real spelling ('semantic', not 'semantic_similarity' — see s010).
-- Category-name validity is not DB-enforced (Problem 8, v094): these
-- are configuration values, not FK-checked identifiers.

INSERT OR IGNORE INTO task_quality_config
    (task_category, metric_type, judge_method, threshold, dual_judge, n_judges)
VALUES
    ('reasoning',        'scalar',    'llm_judge',   0.80, 1, 2),
    ('coding',           'testsuite', 'unit_test',   0.80, 0, 1),
    ('qa',               'binary',    'exact_match', 1.00, 0, 1),
    ('summarization',    'scalar',    'llm_judge',   0.75, 1, 2),
    ('classification',   'binary',    'exact_match', 1.00, 0, 1),
    ('extraction',       'scalar',    'llm_judge',   0.80, 1, 2),
    ('custom',           'scalar',    'llm_judge',   0.70, 0, 1),
    ('multi_tool',       'binary',    'exact_match', 1.00, 0, 1),
    ('planning',         'scalar',    'llm_judge',   0.80, 1, 2),
    ('data_analysis',    'scalar',    'llm_judge',   0.80, 1, 2),
    ('debugging',        'binary',    'exact_match', 1.00, 0, 1),
    ('research',         'scalar',    'llm_judge',   0.75, 1, 2),
    ('orchestration',    'scalar',    'llm_judge',   0.70, 1, 2),
    ('translation',      'scalar',    'semantic',    0.85, 1, 2),
    ('creative_writing', 'scalar',    'llm_judge',   0.70, 1, 2),
    ('web_search',       'binary',    'exact_match', 1.00, 0, 1),
    ('media',            'scalar',    'semantic',    0.80, 0, 1);
