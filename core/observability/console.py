"""
Console renderer for user facing output (SPEC 39.5.2 WP 2e).

Result data a user asked for (summaries, tables, reports) goes through this
renderer on stdout. Operational state goes to logging on stderr (design 39.5.2
section 14 rule 10). Keeping the two apart keeps stdout pipeable for --json.

Rules:
    1. Never used inside the measurement window [t0, t1]; a write to stdout is
       real I/O (master 5.2a). Callers render only after the gate exits.
    2. Never calls logging, so a renderer line can never be duplicated into
       the log or buffered by the gate.
    3. quiet suppresses everything except result(); json suppresses text and
       emits result() objects as one JSON document per call.
"""

import json
import sys
import threading
from typing import Any, Optional

# Modes mirror core.observability.levels so one --quiet or --verbose flag
# drives both channels; only quiet changes renderer behaviour today.
_MODES = ("quiet", "normal", "verbose", "debug")

# ANSI styles for result output; applied only on a TTY, never with --json.
_STYLES = {
    "bold": "\033[1m", "dim": "\033[2m", "cyan": "\033[36m",
    "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "orange": "\033[38;5;208m",
}
_RESET = "\033[0m"


class Console(object):
    """Plain text renderer with fixed column key value lines."""

    def __init__(self, stream=None):
        # stream None means resolve sys.stdout at write time, so pytest
        # capsys and redirect_stdout see the output.
        self._stream = stream
        self._mode = "normal"
        self._json = False
        # Threads (sampler callbacks, executors) may render concurrently;
        # the lock keeps a multi part line from interleaving.
        self._lock = threading.Lock()

    def configure(self, mode=None, json_output=None):
        # type: (Optional[str], Optional[bool]) -> None
        """
        Set mode and json flag; called by observability setup.

        Args:
            mode: one of quiet, normal, verbose, debug; None keeps current.
            json_output: True for --json; None keeps current.
        """
        if mode is not None and mode in _MODES:
            self._mode = mode
        if json_output is not None:
            self._json = bool(json_output)

    @property
    def quiet(self):
        # type: () -> bool
        """True when only results and errors should reach the user."""
        return self._mode == "quiet"

    @property
    def json_output(self):
        # type: () -> bool
        """True when --json is active."""
        return self._json

    def _write(self, text):
        # type: (str) -> None
        """Write one line; never raises, observability is subordinate."""
        stream = self._stream or sys.stdout
        try:
            with self._lock:
                stream.write(text + "\n")
                stream.flush()
        except Exception:
            # A closed pipe (head, less quit) must not fail the run;
            # the same data is in the store and the log.
            pass

    def line(self, text="", indent=0):
        # type: (str, int) -> None
        """
        Render a text line.

        Args:
            text: already formatted text, no emoji.
            indent: indentation level, two spaces each.
        """
        if self._json or self.quiet:
            return
        self._write("  " * indent + text)

    def section(self, title):
        # type: (str) -> None
        """Render a blank line, a title, and an underline."""
        self.line("")
        self.line(title)
        self.line("-" * len(title))

    def kv(self, key, value, indent=1, width=18):
        # type: (str, Any, int, int) -> None
        """
        Render an aligned key value line.

        Args:
            key: label, lower case, no colon.
            value: preformatted value with unit.
            indent: indentation level.
            width: key column width for alignment.
        """
        self.line("%-*s %s" % (width, key, value), indent)

    def style(self, text, name):
        # type: (str, str) -> str
        """
        Wrap text in an ANSI style when stdout is a terminal.

        Plain text on pipes, files, NO_COLOR, and --json; pad text before
        styling so column alignment is unaffected.
        """
        code = _STYLES.get(name)
        if not code or self._json:
            return text
        from core.observability.sinks import use_color
        if not use_color(self._stream or sys.stdout):
            return text
        return code + text + _RESET

    def result(self, obj, text=None):
        # type: (Any, Optional[str]) -> None
        """
        Render a result the user asked for.

        In json mode the object is emitted; otherwise text is rendered, and
        quiet does not suppress it because results are the point of quiet.
        """
        if self._json:
            self._write(json.dumps(obj, default=str, sort_keys=True))
            return
        if text is not None:
            self._write(text)


# One process wide renderer, like the logging root.
_CONSOLE = Console()


def get_console():
    # type: () -> Console
    """Return the process wide console renderer."""
    return _CONSOLE
