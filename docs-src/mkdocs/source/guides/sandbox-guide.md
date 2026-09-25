# A-LEMS Sandbox Guide

**Audience:** Researchers running experiments. No codebase knowledge required.
**Version:** schema v112, engine 1.0.0

---

## What is a Sandbox

A sandbox is your research workspace for one paper or study.
It is a git repo that holds your experiment profiles, overrides, and lock file.
It never holds data, hardware config, endpoints, or credentials.
Those live on the machine.

One sandbox = one `experiments.db` on the machine you run on.
Clone the sandbox repo to any registered machine and run there.
Results stay on that machine under its own data root.

---

## Prerequisites (machine setup, done once per machine)

```bash
# 1. Set your data root in ~/.alemsrc
echo 'export ALEMS_DATA_ROOT=/mnt/alems-data' >> ~/.alemsrc
source ~/.alemsrc

# 2. Set your API endpoints in ~/.alemsrc (never in the sandbox repo)
echo 'export ALEMS_VLLM_API_URL=http://your-server:8000/v1' >> ~/.alemsrc

# 3. Register the engine
cd ~/mydrive/alems-platform
alems engine register .

# 4. Verify engine is registered
alems engine list
```

---

## Create a Sandbox

```bash
# Create sandbox for your paper
alems sandbox create ~/sandboxes/ispass-2027

# That is it. The sandbox is created and active.
# Store is automatically at:
#   /mnt/alems-data/gn100-2b96/sandboxes/ispass-2027-<id>/experiments.db
```

What gets created:

```
~/sandboxes/ispass-2027/
  alems-sandbox.yaml    sandbox identity and engine version
  alems.lock            exact versions of everything (commit this)
  config/
    overrides.yaml      sandbox level overrides (commit this)
  profiles/             your experiment profiles (commit these)
  datasets/             dataset references (commit these)
  extensions/           one-off sandbox extensions
  reports/              generated reports (gitignored)
  .gitignore
```

---

## Write a Profile

A profile is your experiment specification. One file per experiment type.

```bash
cat > ~/sandboxes/ispass-2027/profiles/gsm8k_llama.yaml << 'EOF'
task_id: gsm8k_basic
provider: llama_cpp
model_id: qwen2.5-coder:14b
repetitions: 5
measurement:
  energy: true
  thermal: true
EOF
```

---

## Run an Experiment

```bash
cd ~/sandboxes/ispass-2027
alems run gsm8k_llama
```

The platform:
1. Reads `alems-sandbox.yaml` to find engine version
2. Reads `alems.lock` to verify versions
3. Resolves store at `data_root/hostname/sandboxes/ispass-2027-<id>/experiments.db`
4. Loads `profiles/gsm8k_llama.yaml`
5. Applies config: engine templates < machine config < sandbox overrides < profile
6. Runs experiment, writes results to sandbox store

---

## Switch Between Sandboxes

```bash
# Start working on a different paper
alems sandbox use ~/sandboxes/sigmetrics-2028
alems run retry_test

# Switch back
alems sandbox use ~/sandboxes/ispass-2027
alems run gsm8k_llama
```

---

## Check Sandbox Status

```bash
alems sandbox info
# Shows: name, sandbox_id, engine_version, store path, lock versions

alems sandbox doctor
# Checks: manifest/lock consistency, store exists, engine python exists
```

---

## Share Your Sandbox With a Collaborator

```bash
# In your sandbox repo
git init
git add alems-sandbox.yaml alems.lock profiles/ config/ datasets/ .gitignore
git commit -m "initial sandbox"
git push
```

Collaborator on their machine:

```bash
# Clone the sandbox
git clone <your-repo> ~/sandboxes/ispass-2027
cd ~/sandboxes/ispass-2027

# Their machine needs the same engine version
# If not installed:
python -m venv /opt/alems/envs/1.0.0
/opt/alems/envs/1.0.0/bin/pip install alems-platform==1.0.0
alems engine register /opt/alems/envs/1.0.0

# Run
alems run gsm8k_llama
# Results go to their own data_root, not yours
```

---

## Reproduce a Paper Run

```bash
# Check what engine version the paper used
cat alems.lock | grep runtime_version
# runtime_version: 1.0.0

# Install that exact engine version
python -m venv /opt/alems/envs/1.0.0
/opt/alems/envs/1.0.0/bin/pip install alems-platform==1.0.0
alems engine register /opt/alems/envs/1.0.0

# Clone the paper sandbox
git clone <paper-sandbox-repo> ~/sandboxes/paper-repro

# Run
cd ~/sandboxes/paper-repro
alems run gsm8k_llama
```

The lock file guarantees the same engine, same schema, same plugin versions.
Results are reproducible to within declared measurement tolerance.

---

## Configuration Precedence

Lowest to highest priority:

| Level | Location | What goes here |
|---|---|---|
| 1 | Engine templates | defaults shipped with the engine |
| 2 | Machine config | `~/.alemsrc`, `data_root/hostname/config/` |
| 3 | Sandbox overrides | `config/overrides.yaml` |
| 4 | Profile | `profiles/<name>.yaml` |
| 5 | Command line | `--param value` |

Hardware config, endpoints, and credentials are **only** at level 2.
Never put them in the sandbox repo.

---

## Sandbox vs Store vs Engine

| Concept | What it is | Lives in git |
|---|---|---|
| Engine | Installed alems-platform package + venv | No (pip install) |
| Sandbox | Research workspace: profiles, overrides, lock | Yes |
| Store | `experiments.db` with all run data | No (machine only) |

---

## Common Errors

**`no active sandbox`**
```bash
alems sandbox use ~/sandboxes/ispass-2027
```

**`engine not registered`**
```bash
alems engine register ~/mydrive/alems-platform
```

**`store does not exist`**
First run creates the store automatically. If it fails, check `ALEMS_DATA_ROOT`:
```bash
echo $ALEMS_DATA_ROOT
# Must be set. If empty: source ~/.alemsrc
```

**`lock version mismatch`**
```bash
alems sandbox upgrade --dry-run
# Shows what would change
```
