#!/usr/bin/env bash
# restart_engine.sh: restart the serving engine of one local provider between sessions.
#
# Why: fresh and warm sessions must be chosen, not accidental. The engine is
# stopped and started again through its serve script, which writes a new
# provenance record (PID, start time), so A-LEMS sees a new instance.
# Nothing engine specific lives here: port, worker process pattern, serve
# script and its arguments come from core.serving.engine_config (plugin traits
# < models.yaml serving_engine < ~/.alemsrc). Refuses while an experiment runs.
#
# Usage: restart_engine.sh PROVIDER MODEL
#   Run from the A-LEMS engine root (or a sandbox) with its venv active.
#   READY_TIMEOUT  seconds to wait for the API (default 900)
#   STOP_GRACE_S   seconds between TERM and KILL, and to wait for the port (default 30)
#   GPU_SETTLE_S   seconds for the driver to return GPU memory after stop (default 3)
#   READY_POLL_S   seconds between readiness probes (default 5)
#   EXPERIMENT_PROCESS_PATTERN  regex of experiment processes
#                  (default "run_experiment|alems run|goal_execution")
set -euo pipefail

PROVIDER="${1:?provider, e.g. a local provider from models.yaml}"
MODEL="${2:?model id or path as the serve script expects it}"
TIMEOUT="${READY_TIMEOUT:-900}"; GRACE="${STOP_GRACE_S:-30}"
SETTLE="${GPU_SETTLE_S:-3}"; POLL="${READY_POLL_S:-5}"
EXP_PAT="${EXPERIMENT_PROCESS_PATTERN:-run_experiment|alems run|goal_execution}"

CFG="$(python -m core.serving.engine_config "$PROVIDER")" || { echo "no local engine config for $PROVIDER" >&2; exit 2; }
get() { printf '%s' "$CFG" | python -c "import json,sys; v=json.load(sys.stdin).get('$1'); print('' if v is None else (json.dumps(v) if isinstance(v,(list,dict)) else v))"; }
PORT="$(get port)"; SCRIPT="$(get serve_script)"; PROC_PAT="$(get server_process_pattern)"
ARGS_JSON="$(get serve_args)"; BACKGROUND="$(get serve_script_backgrounds)"; LOG_DIR="$(get provenance_log_dir)"
[ -n "$PORT" ] && [ -n "$SCRIPT" ] && [ -n "$PROC_PAT" ] || { echo "config needs port, serve_script, server_process_pattern: $CFG" >&2; exit 2; }

# An experiment in progress would lose its server mid run: refuse.
if pgrep -af "$EXP_PAT" >/dev/null; then echo "experiment running; not restarting" >&2; exit 3; fi

# Stop only this engine's processes: TERM, then KILL after the grace; wait for the port.
pkill -TERM -f "$PROC_PAT" 2>/dev/null || true
for _ in $(seq 1 "$GRACE"); do pgrep -f "$PROC_PAT" >/dev/null || break; sleep 1; done
if pgrep -f "$PROC_PAT" >/dev/null; then pkill -KILL -f "$PROC_PAT" 2>/dev/null || true; fi
for _ in $(seq 1 "$GRACE"); do ss -ltn | grep -q ":$PORT " || break; sleep 1; done
sleep "$SETTLE"   # let the driver return GPU memory before the next allocation

# serve_args: JSON list with {model} and {port} placeholders, from config.
mapfile -t ARGS < <(printf '%s' "${ARGS_JSON:-[]}" | python -c "
import json,sys; m,p=sys.argv[1:3]
for a in json.load(sys.stdin): print(a.replace('{model}', m).replace('{port}', p))" "$MODEL" "$PORT")
LOG="${LOG_DIR:-.}/restart_${PROVIDER}.log"
if [ "$BACKGROUND" = "True" ] || [ "$BACKGROUND" = "true" ]; then
    bash "$SCRIPT" "${ARGS[@]}"
else
    nohup bash "$SCRIPT" "${ARGS[@]}" >"$LOG" 2>&1 &
fi

# Ready when the OpenAI compatible model list answers.
for t in $(seq 0 "$POLL" "$TIMEOUT"); do
    if curl -s --noproxy '*' --max-time 5 "$(get endpoint)/v1/models" >/dev/null 2>&1; then
        echo "$PROVIDER ready on $PORT after ${t}s"; exit 0
    fi
    sleep "$POLL"
done
echo "$PROVIDER not ready after ${TIMEOUT}s (log $LOG)" >&2; exit 4
