# Writing an A-LEMS Plugin

A-LEMS discovers adapters through Python's standard entry point
mechanism. A plugin is a regular pip-installable package that declares
one or more entry points; A-LEMS finds it automatically at startup.
No core code changes are required to add support for new hardware,
models, platforms, or extensions.

## Minimal plugin layout

```
alems-plugin-yourname/
  pyproject.toml
  alems_plugin_yourname/
    __init__.py       # exports ALEMS_PLUGIN_META
    adapter.py         # your adapter class
```

## pyproject.toml

```toml
[project]
name = "alems-plugin-yourname"
version = "0.1.0"
dependencies = ["alems-platform>=1.0,<2.0"]

[project.entry-points."alems.<group>"]
yourname = "alems_plugin_yourname.adapter:YourAdapter"
```

Replace `<group>` with the family you're extending: `alems.readers.energy`,
`alems.engines.text`, `alems.platforms`, `alems.extensions`, and so on.

## __init__.py

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
standard version specifiers. `platform_constraint` is a coarse OS filter
checked before your adapter is even imported — use it when your plugin
only makes sense on one OS.

## Testing without hardware

```bash
pip install alems-platform
pip install -e .
ALEMS_PLATFORM_OVERRIDE=synthetic alems run --task your_test_task
```

## What A-LEMS guarantees

- Your plugin cannot write to core measurement columns.
- Two plugins claiming the same identity is a startup error, never a
  silent pick.
- A missing or broken plugin never crashes A-LEMS — unless you listed
  it by name in your own active-extensions config, in which case a
  failure is surfaced loudly on purpose.
- Uninstalling your plugin removes it cleanly on the next run.

## Submitting

Open an issue describing what adapter family your plugin targets, test
it against the synthetic platform, then publish to PyPI and submit a
pull request adding it to the plugin catalog.
