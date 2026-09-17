-- migrations/seed/s012_seed_judge_model_set.sql
-- SPEC 35J: populate judge_model_set with real, reachable models, in the
-- SAME {provider, model_id} shape ModelFactory.get_adapter() already
-- expects everywhere else in the platform — no new format invented.
--
-- Required immediately: LLMJudgeScorer's old default judge model tried
-- Ollama (localhost:11434), which is not installed on gn100 (confirmed
-- this session). vllm_local / Mistral-7B-Instruct-v0.3 is the one
-- confirmed-reachable model this session verified with a real call.
--
-- To add or change judge models later: either UPDATE this column (new
-- seed file, since this one becomes immutable per MSC-1 once applied),
-- or override per-experiment via experiment_configs/*.yaml's scoring:
-- block — no code change needed either way, ModelFactory already
-- resolves any provider/model_id pair that exists in models.yaml.

UPDATE task_quality_config
SET judge_model_set = '[{"provider": "vllm_local", "model_id": "Mistral-7B-Instruct-v0.3"}]'
WHERE judge_method IN ('llm_judge', 'semantic');
