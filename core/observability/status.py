"""
In place terminal status line (master amendment 5.2a, console heartbeat).

While a measurement window is open, all observability output is held until t1
(master 5.2a), so a long run would look hung. This module draws one status
line that is rewritten in place once per interval, so the operator always sees
movement:

    | measuring agentic  t+12 s  repetition 2/5 [####------] 40%

Rules (amendment):
    1. One line, carriage return rewrite, erased when the window closes.
       No measured value, no buffered record, no store, file, or network I/O.
    2. Drawn by one daemon thread owned by the gate; measured code never writes.
    3. Only on an interactive terminal and outside quiet mode.
       ALEMS_HEARTBEAT_S sets the interval (default 1 s, 0 disables).
    4. The effective interval is returned to the gate and stored in
       runs.measurement_heartbeat_s (declared perturbation; paper runs use 0).
    5. Never drawn during idle baseline measurement (it would bias idle power).

Outside windows, wait() draws a countdown with a real percentage (cool down).
"""

import os
import sys
import threading
import time
from typing import Any, Dict, Optional

# ASCII spinner: renders on every terminal and font, unlike emoji.
_SPIN = "|/-\\"
_DEFAULT_S = 1.0
ENV = "ALEMS_HEARTBEAT_S"

# Context set by the runner outside the window (phase, repetition).
_ctx = {"phase": None, "rep": None, "total": None}  # type: Dict[str, Any]
_lock = threading.Lock()
_thread = None  # type: Optional[threading.Thread]
_stop = threading.Event()
_stream = None  # injectable for tests; None means the real stderr
_last_len = [0]  # width of the last drawn line, for erasing


def set_context(phase=None, rep=None, total=None):
    # type: (Optional[str], Optional[int], Optional[int]) -> None
    """Record what is running; called by the runner outside the window."""
    with _lock:
        if phase is not None:
            _ctx["phase"] = phase
        if rep is not None:
            _ctx["rep"] = rep
        if total is not None:
            _ctx["total"] = total


def interval():
    # type: () -> float
    """Effective interval in seconds from ALEMS_HEARTBEAT_S (0 disables)."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return _DEFAULT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_S


def _target():
    """The stream drawn on: the real stderr, never a redirected sys.stderr."""
    return _stream or sys.__stderr__


def enabled():
    # type: () -> bool
    """True when a status line may be drawn: interval set, TTY, not quiet."""
    if interval() <= 0:
        return False
    try:
        if not _target().isatty():
            return False
    except Exception:  # noqa: BLE001  a stream without isatty is not a TTY
        return False
    try:
        from core.observability.console import get_console
        return not get_console().quiet
    except Exception:  # noqa: BLE001
        return True


def _bar(done, total, width=10):
    # type: (float, float, int) -> str
    """Progress bar and percentage for a known total."""
    frac = 0.0 if total <= 0 else min(max(done / total, 0.0), 1.0)
    filled = int(round(frac * width))
    return "[%s%s] %3d%%" % ("#" * filled, "-" * (width - filled), int(frac * 100))


def _draw(text):
    # type: (str) -> None
    """Rewrite the status line in place; never raises (observability is subordinate)."""
    pad = max(0, _last_len[0] - len(text))
    try:
        stream = _target()
        stream.write("\r" + text + " " * pad)
        stream.flush()
        _last_len[0] = len(text)
    except Exception:  # noqa: BLE001
        pass


def _clear():
    """Erase the status line so released log lines start at column 0."""
    if _last_len[0]:
        try:
            # ANSI erase line: no leftover blanks for copy and paste.
            _target().write("\r\033[2K\r")
            _target().flush()
        except Exception:  # noqa: BLE001
            pass
        _last_len[0] = 0


def _render(elapsed, tick):
    # type: (float, int) -> str
    """Compose the in window status text from context and elapsed time."""
    with _lock:
        phase, rep, total = _ctx["phase"], _ctx["rep"], _ctx["total"]
    text = "%s measuring %s  t+%d s" % (_SPIN[tick % len(_SPIN)], phase or "", int(elapsed))
    if rep and total:
        text += "  repetition %d/%d" % (rep, total)
        # Bar only across several repetitions: completed ones are real progress.
        # A single repetition has no known length, so no bar (it would sit at 0%).
        if total > 1:
            text += " " + _bar(rep - 1, total)
    return text


def _loop(iv, start):
    # type: (float, float) -> None
    """Status thread: draw now, then once per interval until stopped."""
    tick = 0
    while True:
        _draw(_render(time.perf_counter() - start, tick))
        tick += 1
        if _stop.wait(iv):
            return


def window_start():
    # type: () -> float
    """
    Start the status line for a measurement window (called by gate.enter).

    Returns:
        The effective interval in seconds, 0 when no line is drawn.
    """
    window_stop()  # a window left open by an exception is closed first
    if not enabled():
        return 0.0
    global _thread
    iv = interval()
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(iv, time.perf_counter()),
                               daemon=True, name="alems-status")
    _thread.start()
    return iv


def window_stop():
    # type: () -> None
    """Stop and erase the status line (called by gate.exit before release)."""
    global _thread
    if _thread is None:
        return
    _stop.set()
    _thread.join(timeout=2.0)
    _thread = None
    _clear()


def wait(seconds, label):
    # type: (float, str) -> None
    """
    Sleep for seconds, drawing a countdown bar outside any window.

    Same total sleep as time.sleep(seconds); without a terminal it only sleeps.
    """
    if seconds <= 0:
        return
    if not enabled():
        time.sleep(seconds)
        return
    end = time.perf_counter() + seconds
    while True:
        left = end - time.perf_counter()
        if left <= 0:
            break
        _draw("%s %s  %d s left" % (label, _bar(seconds - left, seconds), int(left + 0.999)))
        time.sleep(min(interval(), left))
    _clear()
