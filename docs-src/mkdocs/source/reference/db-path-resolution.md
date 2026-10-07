# Database Path Resolution

---
**Status:** PRODUCTION
**Applies to:** every command that opens a store
**Last updated:** 2026-10-06
---

## Overview

Every A-LEMS command works on exactly one store (an SQLite database). This page is the reference for how that store is found. The layout of sandbox stores, the sandbox machine file `.sandbox-env`, and worked examples are in the user guide page Sandbox Layout and Paths.

## Resolution Chain

Highest priority first; the first match wins:

| Order | Source | Typical use |
|---|---|---|
| 1 | `--store <path>` or `--sandbox <path>` on the command line | one off commands on a specific store |
| 2 | `ALEMS_STORE` in the environment (shell, or `.sandbox-env`) | a pinned store for a sandbox |
| 3 | `ALEMS_SANDBOX` in the environment | select a sandbox without changing directory |
| 4 | the nearest `alems-sandbox.yaml` above the current directory | normal work inside a sandbox |
| 5 | the active sandbox, `<data_root>/<host>/users/<user>/active-sandbox` | set by `alems sandbox use` |
| 6 | the engine environment layout | running from the engine checkout |
| 7 | an error naming every source checked | nothing configured |

`.sandbox-env` is read at startup; a variable already set in the shell wins over the file.

## Store Locations

| Store | Path |
|---|---|
| Sandbox (current layout) | `<data_root>/<host>/users/<user>/sandboxes/<name>/experiments.db` |
| Sandbox (created before the per user layout) | `<data_root>/<host>/sandboxes/<name>/experiments.db` |
| Engine environment | `<data_root>/<host>/envs/<user>/<env>/<project>/experiments.db` |

Logs and error records always sit beside the store: `<store dir>/logs/` and `<store dir>/errors/`.

`<data_root>` comes from `ALEMS_DATA_ROOT` (normally in `~/.alemsrc`) or the `data_root` key of the sandbox manifest. A run without a data root stops with `ALEMS-CFG-0010`.

## Debug Commands

```bash
alems sandbox info                     # store, engine, lock of the current sandbox
alems sandbox doctor                   # checks that manifest, lock, store, and .sandbox-env agree
python3 -c "from core.storage.resolver import resolve_store; print(resolve_store())"
python3 -c "from core.storage.resolver import resolve_data_root; r=resolve_data_root(); print(r[0], r[1])"
```

## Common Problems

| Symptom | Cause | Fix |
|---|---|---|
| a run starts on an empty store | the store was moved but the manifest `store` key or `ALEMS_STORE` still names the old path | update both, then `alems sandbox doctor` |
| `ALEMS-CFG-0010` | no data root configured | set `ALEMS_DATA_ROOT` in `~/.alemsrc` or `data_root` in the manifest |
| a different store than expected | a higher source in the chain wins (often `ALEMS_STORE` exported in the shell) | `env \| grep ALEMS_` and unset the stale variable |
| store path names a symlink | legacy engine layouts may link to a shared file | the path printed by the resolver is the identity used for logs and errors |

## Known Limitations

- **Location recorded in more than one place:** the manifest `store` key and `ALEMS_STORE` both record a sandbox store; keep them in agreement when moving a store.
