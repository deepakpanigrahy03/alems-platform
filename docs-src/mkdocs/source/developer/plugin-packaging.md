# A-LEMS Plugin Architecture

A-LEMS discovers adapters through Python's standard entry point
mechanism (`importlib.metadata.entry_points()`), the same mechanism
pytest, Flask, and pip itself use for their own plugin ecosystems. A
plugin is a regular pip-installable package. No core code changes are
required to add support for new hardware, models, or platforms.

## How discovery works

A plugin declares what it provides in its own `pyproject.toml`:

```toml
[project.entry-points."alems.engines.text"]
ollama = "alems_plugin_ollama.adapter:OllamaAdapter"
```

This is metadata, not code that runs on its own. When the package is
installed with `pip`, this declaration is written into the package's
installed metadata, where it becomes queryable without importing the
package.

At startup, A-LEMS asks Python's package system what's installed:

```python
from importlib.metadata import entry_points
entry_points(group="alems.engines.text")
```

This scans every installed package on the machine and returns anything
registered under that group — built-in or third-party, indistinguishably.
Only when A-LEMS actually needs a specific adapter does it import it
(`entry_point.load()`), so a broken or incompatible plugin can never
crash startup for everyone else — it's skipped, and everything else
loads normally.

The resolved class lands in the exact same registry every built-in
adapter uses. From the platform's point of view there is no difference
between a built-in reader and a pip-installed one.

## Adapter families available today

| Entry point group | What it adds |
|---|---|
| `alems.readers.energy` | Hardware energy counters |
| `alems.readers.cpu` | CPU performance counters |
| `alems.readers.thermal` | Thermal sensors |
| `alems.readers.turbostat` | Turbostat / frequency scaling readers |
| `alems.readers.msr` | Model-specific register readers |
| `alems.readers.scheduler` | Scheduler activity monitors |
| `alems.readers.disk` | Disk I/O readers |
| `alems.engines.text` | Text-generation serving engines |
| `alems.engines.media` | Speech/audio serving engines |
| `alems.platforms` | Hardware platform detection and provisioning |
| `alems.extensions` | Post-run research extensions |
| `alems.scorers` | Quality evaluation scorers |
| `alems.tools` | Agentic tool providers |
| `alems.tool_selectors` | Tool selection strategies (which tools an agent sees) |
| `alems.frameworks` | Agent framework adapters (LangChain, CrewAI, etc.) |
| `alems.outputs` | Export/output format adapters |

Database backends discover external plugins the same way, but ship no
built-in example yet beyond SQLite. Scorers, tools, tool selectors,
frameworks, and outputs are available today — see the table above,
and the dedicated [Adding a Tool Provider](adding-a-tool-provider.md)
guide for a full worked example.

## Minimal plugin layout

```
alems-plugin-yourname/
  pyproject.toml
  alems_plugin_yourname/
    __init__.py       # exports ALEMS_PLUGIN_META
    adapter.py         # your adapter class
```

### pyproject.toml

```toml
[project]
name = "alems-plugin-yourname"
version = "0.1.0"
dependencies = ["alems-platform>=1.0,<2.0"]

[project.entry-points."alems.<group>"]
yourname = "alems_plugin_yourname.adapter:YourAdapter"
```

### __init__.py

Every plugin package must export `ALEMS_PLUGIN_META`:

```python
ALEMS_PLUGIN_META = {
    "name": "yourname",
    "version": "0.1.0",
    "alems_compat": ">=1.0,<2.0",
    "description": "What your plugin does",
    "platform_constraint": None,   # None, "linux", or ["linux", "darwin"]
}
```

`alems_compat` declares which core versions your plugin supports, using
standard version specifiers — checked before your adapter is even
imported. `platform_constraint` is a coarse OS filter for the same
purpose, checked using `sys.platform`.

Note: `platform_constraint` only answers "can this run on this OS at
all." Fine-grained hardware eligibility (does this specific machine
have the sensor this reader needs) is a separate check your adapter
class implements itself, evaluated later, only when A-LEMS is choosing
between multiple eligible adapters for the same measurement.

### Reader/platform classes vs. engine classes

Reader and platform adapters are selected by hardware capability probe
(the adapter with the highest priority that reports it can handle the
current machine wins). Engine adapters are selected by an exact name
match against your experiment's provider configuration, so an engine
class needs to declare both an identity used for registration and the
identity used for lookup — check an existing built-in adapter class in
the same family for the exact attribute names your base class expects.

Scorers, tool providers, tool selectors, frameworks, and outputs all
follow the engine pattern, not the reader pattern — exact identity-
string match (`SCORER_TYPE`, `TOOL_PROVIDER_TYPE`, `SELECTOR_TYPE`,
`FRAMEWORK_TYPE`, `OUTPUT_FORMAT`), never a capability probe. Whatever
your task configuration names by string is exactly what gets looked
up — there's no "best adapter for this situation" logic to satisfy in
these families, only "does something register under this exact name."

## Testing without hardware

```bash
pip install alems-platform
pip install -e .
ALEMS_PLATFORM_OVERRIDE=synthetic alems run --task your_test_task
```

## Plugin configuration

If your plugin needs configuration, declare a `get_config_schema()`
classmethod on your adapter class:

```python
@classmethod
def get_config_schema(cls) -> dict:
    return {
        "timeout_ms": {
            "type": "int",
            "required": False,
            "default": 30000,
        },
        "judge_model": {
            "type": "str",
            "required": True,
            "default": None,
        },
    }
```

The researcher adds a matching section to `config/app_settings.yaml`:

```yaml
plugins:
  yourname:
    timeout_ms: 60000
    judge_model: "gpt-4o-mini"
```

The platform validates this at startup and injects a typed dict into
your adapter before any experiment runs. Your adapter never reads
config files directly.

Valid types: `"str"`, `"int"`, `"float"`, `"bool"`.
`required: true` with no `default` means startup fails with a clear
message if the key is absent.
Unknown keys in the YAML section are warned and ignored.

## If Your Plugin Needs New Database Tables

A tool provider, scorer, framework adapter, or output format that
needs its own table doesn't declare that table itself — it ships as,
or alongside, an extension (see the [Extension System
guide](extension-system.md)), since only extensions own migrations
and activation lifecycle.

`pip install`ing such a plugin never touches a database by itself —
installation only makes your package's code and metadata visible to
Python; nothing runs automatically. The next time `alems dev sync`
runs on a machine, it checks every installed extension's entry point
against that machine's `[extensions] active` list in
`app_settings.yaml`. An installed-but-not-yet-activated extension is
reported plainly:

⚠ New extension detected: 'yourname' (installed, not active)
Migrations pending: e001_create_yourtable.sql
To activate: add "yourname" to app_settings.yaml [extensions] active,
then re-run 'alems dev sync'.


This is deliberate, not a missing feature: a research measurement
platform should never silently change its own schema the moment a
package lands in `site-packages`. Detection is automatic every time
you sync; activation is always one explicit, reviewable line you add
yourself, whenever you're ready to look at the actual migration file
first.
## What A-LEMS guarantees

- Your plugin cannot write to core measurement columns.
- Two adapters claiming the same identity is a startup error, never a
  silent pick — whichever installed second fails loudly.
- A missing or broken plugin never crashes A-LEMS, unless you
  explicitly listed it by name in your own active-extensions
  configuration, in which case a failure is surfaced loudly on purpose
  because you asked for it by name.
- Uninstalling your plugin removes it cleanly on the next run — nothing
  else changes.

## Submitting

Open an issue describing what adapter family your plugin targets, test
it against the synthetic platform, then publish to PyPI and submit a
pull request adding it to the plugin catalog.
