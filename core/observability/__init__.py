"""
core/observability: logging core (39.5.2 WP 2a).

Public API used by entry points and runtime code. Everything else in the
package is internal. Observability is subordinate to the application:
no function here raises into the caller for an observability failure
(master 5.2a).
"""

from core.observability.context import bind, get_context, set_base_context
from core.observability.extras import register_extra_keys
from core.observability.setup import flush_run_log, setup_logging, shutdown_logging

__all__ = [
    "bind",
    "get_context",
    "set_base_context",
    "register_extra_keys",
    "setup_logging",
    "flush_run_log",
    "shutdown_logging",
]
