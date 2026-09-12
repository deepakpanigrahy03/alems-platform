# Quick Start

This guide assumes [Installation](installation.md) is complete and
`alems dev status` shows your platform and schema version without errors.

---

## Configure a Provider

Before running experiments, add at least one API key or server URL to
`~/.alemsrc`. See [API Keys](api-keys.md) for the full provider list.

The fastest path is Groq, which has a free tier and no local setup:

```bash
echo 'export GROQ_API_KEY=gsk_...' >> ~/.alemsrc
source ~/.alemsrc
```

For a local vllm server running on the same or another machine:

```bash
echo 'export ALEMS_VLLM_REMOTE_URL=http://100.84.85.2:8000/v1' >> ~/.alemsrc
source ~/.alemsrc
```

---

## Run Your First Experiment

```bash
cd alems-platform && source venv/bin/activate

python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 1 \
  --provider groq \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "first experiment" \
  --save-db
```

`gsm8k_basic` runs grade school math problems. `--workflow-mode comparison`
runs both linear (single LLM call) and agentic (planning + tools + synthesis)
workflows so you see both energy profiles in one run.

A run takes 2 to 5 minutes depending on hardware and provider latency.

---

## Key Arguments

| Argument | What it does |
|---|---|
| `--tasks` | Task ID from `--list-tasks` |
| `--provider` | Provider name from models.yaml |
| `--workflow-mode` | `linear`, `agentic`, or `comparison` |
| `--repetitions` | Number of times to repeat each task |
| `--experiment-type` | `normal`, `debug`, `overhead_study`, `retry_study`, `pilot` |
| `--experiment-goal` | Human readable description stored in DB |
| `--save-db` | Write results to the database |
| `--verbose` | Print hardware counters per repetition |

---

## List Available Tasks

```bash
python3 core/execution/tests/run_experiment.py --list-tasks
```

65 tasks across arithmetic, reasoning, code, QA, summarization,
tool chains, multi-step agentic, TTS, STT, and voice cloning.

---

## Reading the Output

```
   📋 groq | GSM8K Arithmetic
   ✅ groq | gsm8k_basic complete:
      linear_energy_uj:   4821
      agentic_energy_uj:  9347
      orchestration_uj:   1203  (12.9%)
      duration_s:         3.4
      ipc:                2.41
      instructions:       8200000000
```

**energy_uj** is microjoules. 1,000,000 µJ = 1 J.

**orchestration_uj** is the energy cost of agentic coordination: planning,
tool dispatch, and synthesis on top of the raw inference calls. Expressed as
a percentage of total agentic energy, this is the orchestration tax.

**ipc** is instructions per cycle. Higher values indicate better CPU
utilization during inference.

On modeled platforms (linux_arm, linux_x86_unknown), energy values are 0.
The measurement tier in the output confirms this.

---

## Where Results Are Stored

```bash
# Find your database path
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
"
```

Query recent runs directly:

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

sqlite3 "$DB" "
SELECT run_id, workflow_type, total_energy_uj, duration_ns/1e9 as seconds
FROM runs
ORDER BY run_id DESC
LIMIT 10;
"
```

---

## Run Against Multiple Providers

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 3 \
  --providers groq,vllm_remote \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "provider comparison" \
  --save-db
```

`--providers` (plural) accepts a comma-separated list.

---

## Troubleshooting a Failed Run

**Provider not found:**

```bash
python3 core/execution/tests/run_experiment.py --list-tasks
# If this works, check provider name spelling against models.yaml
grep "^  [a-z]" config/models.yaml
```

**vllm unreachable:**

```bash
curl -s --connect-timeout 3 \
  "${ALEMS_VLLM_REMOTE_URL}/models" | python3 -m json.tool | head -10
```

**Database errors:**

```bash
alems dev sync
```

For hardware reader failures see [Troubleshooting](05-troubleshooting.md).
