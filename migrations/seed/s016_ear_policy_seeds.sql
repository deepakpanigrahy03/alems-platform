-- s016_ear_policy_seeds.sql
-- EAR reference policy definitions. Universal, all platforms.
-- Four named policies covering the full retry aggressiveness spectrum.
-- ear_policy_rules join via ear_policy_id FK — subquery resolves name to id.
-- flat_default: NULL thresholds — reference label only, never loaded by EARAdapter.
-- ear_conservative: success_prob >= 0.60, cost <= 1.5x mean.
-- ear_v1: calibrated balanced defaults from failure_cost_profile heuristics.
-- ear_aggressive: success_prob >= 0.05, cost <= 5x mean.

-- ── ear_policy rows ──────────────────────────────────────────────────────────

INSERT OR IGNORE INTO ear_policy (policy_name, description, calibration_scope) VALUES
('flat_default',    'Flat retry baseline — no energy awareness. Always retries retryable types up to max_attempts. Reference label for policy_comparison_results. Never loaded by EARAdapter.', 'experiment_group'),
('ear_conservative','Energy-Aware Retry conservative — success_prob_threshold=0.60, cost_ceiling=1.5x mean. Retries only when recovery is highly likely and cheap.', 'experiment_group'),
('ear_v1',          'Energy-Aware Retry v1 — calibrated balanced policy. Heuristics: max_attempts=ceil(1/success_rate) cap 5, cost_threshold=mean*2.0, success_prob_threshold=0.10.', 'experiment_group'),
('ear_aggressive',  'Energy-Aware Retry aggressive — success_prob_threshold=0.05, cost_ceiling=5x mean. Retries until budget exhausted. Maximises success rate at higher energy cost.', 'experiment_group');

-- ── ear_policy_rules: flat_default ──────────────────────────────────────────

INSERT OR IGNORE INTO ear_policy_rules (ear_policy_id, failure_type_id, max_attempts, cost_threshold_uj, success_probability_threshold, calibrated_success_prob, action, priority) VALUES
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'tool_error',       3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'api_error',        3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'rate_limit',       3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'timeout',          3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'network_error',    3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'json_parse',       3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'malformed_output', 3, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'auth_error',       1, NULL, NULL, NULL, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'not_found',        1, NULL, NULL, NULL, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'capability_error', 1, NULL, NULL, NULL, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'hallucination',    2, NULL, NULL, NULL, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'semantic_error',   1, NULL, NULL, NULL, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'malformed_input',  1, NULL, NULL, NULL, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='flat_default'), 'crashed',          1, NULL, NULL, NULL, 'abort', 0);

-- ── ear_policy_rules: ear_conservative ──────────────────────────────────────

INSERT OR IGNORE INTO ear_policy_rules (ear_policy_id, failure_type_id, max_attempts, cost_threshold_uj, success_probability_threshold, calibrated_success_prob, action, priority) VALUES
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'tool_error',       2, 120000000, 0.60, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'api_error',        2, 120000000, 0.60, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'rate_limit',       2, 200000000, 0.60, 0.75, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'timeout',          2, 150000000, 0.60, 0.60, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'network_error',    2, 120000000, 0.60, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'json_parse',       2, 100000000, 0.60, 0.80, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'malformed_output', 2, 100000000, 0.60, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'auth_error',       1,  50000000, 0.90, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'not_found',        1,  50000000, 0.90, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'capability_error', 1,  50000000, 0.90, 0.05, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'hallucination',    2, 150000000, 0.60, 0.50, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'semantic_error',   1, 100000000, 0.90, 0.20, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'malformed_input',  1,  50000000, 0.90, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_conservative'), 'crashed',          1, 200000000, 0.90, 0.05, 'abort', 0);

-- ── ear_policy_rules: ear_v1 ─────────────────────────────────────────────────

INSERT OR IGNORE INTO ear_policy_rules (ear_policy_id, failure_type_id, max_attempts, cost_threshold_uj, success_probability_threshold, calibrated_success_prob, action, priority) VALUES
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'tool_error',       2, 164000000, 0.10, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'api_error',        2, 164000000, 0.10, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'rate_limit',       2, 300000000, 0.10, 0.75, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'timeout',          3, 200000000, 0.10, 0.60, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'network_error',    2, 164000000, 0.10, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'json_parse',       2, 140000000, 0.10, 0.80, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'malformed_output', 2, 140000000, 0.10, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'auth_error',       1, 100000000, 0.10, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'not_found',        1, 100000000, 0.10, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'capability_error', 1, 100000000, 0.10, 0.05, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'hallucination',    2, 200000000, 0.10, 0.50, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'semantic_error',   1, 140000000, 0.10, 0.20, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'malformed_input',  1, 100000000, 0.10, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_v1'), 'crashed',          1, 300000000, 0.10, 0.05, 'abort', 0);

-- ── ear_policy_rules: ear_aggressive ─────────────────────────────────────────

INSERT OR IGNORE INTO ear_policy_rules (ear_policy_id, failure_type_id, max_attempts, cost_threshold_uj, success_probability_threshold, calibrated_success_prob, action, priority) VALUES
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'tool_error',       5, 410000000, 0.05, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'api_error',        5, 410000000, 0.05, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'rate_limit',       5, 750000000, 0.05, 0.75, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'timeout',          5, 500000000, 0.05, 0.60, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'network_error',    5, 410000000, 0.05, 0.65, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'json_parse',       5, 350000000, 0.05, 0.80, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'malformed_output', 5, 350000000, 0.05, 0.70, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'auth_error',       2, 200000000, 0.05, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'not_found',        2, 200000000, 0.05, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'capability_error', 2, 200000000, 0.05, 0.05, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'hallucination',    3, 500000000, 0.05, 0.50, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'semantic_error',   2, 350000000, 0.05, 0.20, 'retry', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'malformed_input',  2, 200000000, 0.05, 0.10, 'abort', 0),
((SELECT ear_policy_id FROM ear_policy WHERE policy_name='ear_aggressive'), 'crashed',          2, 600000000, 0.05, 0.05, 'abort', 0);
