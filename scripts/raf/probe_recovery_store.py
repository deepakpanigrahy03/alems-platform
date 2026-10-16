"""Print the store values the R5 export depends on (snapshot only, read only)."""
import sqlite3
import sys

db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
Q = [
    ("energy_domains", "select * from energy_domains"),
    ("idle domain names", "select distinct domain_name from v_idle_baseline_domains"),
    ("judge methods", "select judge_method, count(*) from output_quality group by 1"),
    ("injection status", "select status, injected_type, count(*) from failure_injection_log group by 1,2"),
    ("attempt failure_type", "select failure_type, count(*) from goal_attempt group by 1"),
    ("gpu sources", "select source, count(*) from gpu_samples group by 1"),
    ("domain 28 sample", "select d.energy_uj, s.interval_ns from energy_sample_domains d "
                         "join energy_samples_v2 s on s.sample_id=d.sample_id where d.domain_id=28 limit 5"),
    ("llm call timing", "select run_id, step_index, prompt_tokens, request_start_ns, "
                        "last_token_time_ns from llm_interactions limit 3"),
    ("goal 479", "select a.attempt_id, a.attempt_number, a.run_id, a.failure_type, a.pass_fail, "
                 "r.llm_calls, r.tool_calls, r.baseline_id from goal_attempt a join runs r "
                 "on r.run_id=a.run_id where a.goal_id=479"),
]
for name, sql in Q:
    print(f"\n== {name}")
    try:
        for row in c.execute(sql).fetchall()[:40]:
            print(row)
    except Exception as e:
        print("ERROR", e)
