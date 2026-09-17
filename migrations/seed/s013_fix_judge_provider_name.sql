-- migrations/seed/s013_fix_judge_provider_name.sql
-- SPEC 35J: s012 used provider "vllm_local" (base_url:
-- http://localhost:8000/v1) by mistake, copied from an unrelated
-- example. The confirmed-reachable endpoint this session actually
-- tested against is "vllm_remote" (base_url: $ALEMS_VLLM_REMOTE_URL,
-- resolves to http://100.84.85.2:8000/v1 on gn100). Fix forward,
-- per MSC-1 — s012 itself is not edited.

UPDATE task_quality_config
SET judge_model_set = '[{"provider": "vllm_remote", "model_id": "Mistral-7B-Instruct-v0.3"}]'
WHERE judge_method IN ('llm_judge', 'semantic')
  AND judge_model_set = '[{"provider": "vllm_local", "model_id": "Mistral-7B-Instruct-v0.3"}]';
