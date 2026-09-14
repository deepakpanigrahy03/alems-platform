"""
================================================================================
STARTUP BANNER — Adapter Registration Summary
================================================================================

PURPOSE:
    Print a one-line, human-readable summary of what got registered in
    each adapter family, at the moment registration finishes.

    Uses plain print(), not logging. Registration for readers happens
    at module import time in core/energy_engine.py (line ~45), before
    any logging.basicConfig() call exists in the process — logger.info()
    calls at that point go nowhere. print() always shows.

AUTHOR: Deepak Panigrahy
================================================================================
"""

# Guards against printing the same family's summary twice if a bootstrap
# module's register_all_*() is called more than once in one process
# (this already happens for engines — model_factory.py's own guard
# checks is_empty() before calling register_all_adapters() again).
_PRINTED: set = set()


def print_adapter_summary(
    component: str,
    builtin_names,
    external_names,
) -> None:
    """
    Print one line summarizing what was registered for a component.

    Args:
        component: Short label, e.g. "readers", "engines", "platforms".
        builtin_names: Iterable of built-in adapter identity strings
            already in the registry before external discovery ran.
        external_names: Iterable of adapter identity strings that came
            from pip-installed plugins via entry_points.
    """
    if component in _PRINTED:
        return
    _PRINTED.add(component)

    builtin_names = list(builtin_names)
    external_names = list(external_names)
    total = len(builtin_names) + len(external_names)

    if external_names:
        print(
            f"[A-LEMS] {component}: {total} adapter(s) registered "
            f"({len(builtin_names)} built-in, {len(external_names)} plugin: "
            f"{', '.join(sorted(external_names))})"
        )
    else:
        print(f"[A-LEMS] {component}: {total} adapter(s) registered (built-in only)")
