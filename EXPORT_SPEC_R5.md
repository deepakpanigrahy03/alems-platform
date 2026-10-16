# R5 needs from the shared read only export (E1 plus E1b), for merge with R7

Store opened with `file:<db>?mode=ro` once, after the coordinator says E1b is done.
Energy per Addendum D3: board DC_INPUT (domain 28, mW) as mean times duration, idle from `v_idle_baseline_domains` through `runs.baseline_id`, other domains summed per interval, DCGM from `gpu_samples` summed.
Task names normalized (strip `bfcl_`).

## goals.csv: one row per (goal, attempt)

| column | meaning |
|---|---|
| goal_id, task, cls, point, option | option as FR_warm, SR_cold, RB_warm, SG_cold, B0 |
| attempt | 1, 2, 3 |
| e_board_j | board dynamic energy of the attempt |
| e_pkg_j | PACKAGE dynamic energy (secondary) |
| calls_total, calls_model | model plus tool calls, model calls only, completed in the attempt |
| turns | completed turns in the attempt |
| last_prompt_tokens | prompt tokens of the last model call of the attempt |
| tok_billed, tok_uncached | summed over the attempt |
| pass_fail | output_quality.pass_fail of this attempt (judge bfcl_state) |

## calls.csv: one row per model call (fits e(c))

| column | meaning |
|---|---|
| goal_id, task, attempt, call_index | |
| context_tokens, prompt_tokens, completion_tokens, cached_tokens | |
| e_board_j | board dynamic energy over the call interval |
