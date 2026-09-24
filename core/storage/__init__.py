"""
core/storage — storage contract implementation package (39.2).

Public API:
    resolve_store()       -- canonical store path lookup
    resolve_hw_config()   -- canonical hw_config.json path lookup
    hw_config_write_paths() -- dual-write targets for detect_hardware
    set_active_project()  -- record active project for 'alems project use'
    clear_active_project()
    InProcessWriter       -- WriterSession backed by SQLiteAdapter
    open_writer()         -- convenience factory
"""

from core.storage.resolver import (  # noqa: F401
    resolve_store,
    resolve_hw_config,
    hw_config_write_paths,
    set_active_project,
    clear_active_project,
)
from core.storage.inprocess_writer import InProcessWriter  # noqa: F401


def open_writer(
    explicit_store: str = None,
    lock_timeout_s: float = 30.0,
) -> InProcessWriter:
    """
    Convenience factory: resolve the store path and return an unopened
    InProcessWriter.

    Call open() or use as a context manager before writing.

    Args:
        explicit_store: override path (--store / --project flag value).
        lock_timeout_s: seconds to wait for the project lock.

    Returns:
        InProcessWriter (not yet opened).
    """
    store_path = resolve_store(explicit=explicit_store)
    return InProcessWriter(store_path, lock_timeout_s=lock_timeout_s)
