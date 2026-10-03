# Sandbox and Engine { #sandbox-and-engine }

## Two places, two roles

| | Engine | Sandbox |
|---|---|---|
| what | the installed A-LEMS: code, plugins, venv, machine facts (hw_config.json) | your research workspace: profiles, overrides, lock file; a git repo |
| who changes it | the platform maintainer only | the researcher |
| store | a developer store for engine testing | the sandbox store, where experiment data lives |

Researchers always work inside a sandbox. Data never lives in the sandbox folder; its store sits under the data root of the machine.

## Daily commands

```bash
cd <your sandbox>
alems sandbox info              # sandbox, store, lock, manifest engine and running engine
alems run <profile name>        # profile name without folder or .yaml
alems validate invariants --since <n>
alems validate persistence      # duplicate and orphan rows
```

`alems sandbox info` warns when the engine named in the manifest differs from the engine actually running.

## Upgrading a sandbox after an engine update

```bash
cd <your sandbox>
alems sandbox upgrade           # shows engine, store schema, lock schema, pending migrations
alems sandbox upgrade --run     # backup, migrate, update lock
```

If an upgrade stops with a duplicate rows message, repair first (see Store Repair) and run the upgrade again.

## Checking the engine environment

```bash
alems dev status
```

Shows the interpreter, that the SDK comes from this engine, user site packages disabled, installed plugins, and the resolved store and schema version.
