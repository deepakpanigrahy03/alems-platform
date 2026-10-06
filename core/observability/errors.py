"""
Error capture and the C-ERR error store (master 5.3, design 39.5.2d).

capture() never raises and never alters control flow. Inside the window
gate it only builds a small in memory record and submits it to the gate's
non lossy "error" buffer; formatting, classification by traceback,
redaction, hashing, and the file write happen in the consumer after t1.
Outside the gate the same consumer runs immediately.

Records: <error_dir>/<yyyy-mm-dd UTC>/<error_id>.json, written atomically.
error_ref semantics: an allocated error_id. If the gate refused the record
for overflow, no file exists and observability_overflow = 1 explains it.
"""
import contextvars
import hashlib
import json
import logging
import os
import socket
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.errors import GENERIC_CODE
from core.observability import context, error_catalog, gate, locations, redact

logger = logging.getLogger(__name__)
RECORD_FORMAT = 1
CAPTURE_FAILED_CODE = "ALEMS-OBS-0001"
# Engine root, used to make traceback paths host independent for hashing.
_ENGINE_ROOT = str(Path(__file__).resolve().parents[2])
# (recorder, stage_id) of the stage currently executing, set by StageRecorder.
_ACTIVE_STAGE = contextvars.ContextVar("alems_active_stage", default=None)


def push_stage(recorder: Any, stage_id: str) -> contextvars.Token:
    """Mark a stage active so swallowed failures inside it are noted on it."""
    return _ACTIVE_STAGE.set((recorder, stage_id))


def pop_stage(token: contextvars.Token) -> None:
    """Undo push_stage; tolerant of tokens from another context."""
    if token is None:
        return  # push failed or no op shim
    try:
        _ACTIVE_STAGE.reset(token)
    except (ValueError, LookupError, TypeError):
        _ACTIVE_STAGE.set(None)


def _count_failure() -> None:
    """Increment the capture_failed counter kept with the gate counters."""
    gate.COUNTERS["capture_failed"] = gate.COUNTERS.get("capture_failed", 0) + 1


def _ensure_consumer() -> None:
    """
    Register the file consumer once per gate lifetime.

    gate.reset_for_tests may clear consumers, so membership is checked
    rather than a module flag (reads gate._consumers deliberately).
    """
    if _write_records not in gate._consumers.get("error", []):  # noqa: SLF001
        gate.register_consumer("error", _write_records)


def capture(exc: BaseException, code: Optional[str] = None,
            component: Optional[str] = None, recoverable: Optional[bool] = None,
            note: bool = True) -> Optional[str]:
    """
    Capture an exception as a C-ERR record.

    Args:
        exc: the exception (not raised again here; callers keep control flow).
        code: site declared code (classification tier 2).
        component: logger style name of the capturing module.
        recoverable: site override, honoured only toward False.
        note: attach to the active stage (False when the stage itself captures).

    Returns:
        error_id, or None when capture failed (original exception unaffected).
    """
    return capture_coded(exc, code, component, recoverable, note)[0]


def capture_coded(exc: BaseException, code: Optional[str] = None,
                  component: Optional[str] = None, recoverable: Optional[bool] = None,
                  note: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """
    capture() returning (error_id, code); code is None inside the window
    unless the site declared one (classification happens at flush there).
    """
    active = _ACTIVE_STAGE.get()
    try:
        _ensure_consumer()
        ctx = context.get_context()
        inside = gate.is_inside()
        rec = {
            "error_id": str(uuid.uuid4()), "declared_code": code, "component": component,
            "stage_id": active[1] if active else ctx.get("stage_id"),
            "ts": datetime.now(timezone.utc).isoformat(), "event_seq": gate.next_seq(),
            "context": dict(ctx), "pid": os.getpid(), "exc": exc,
            "recoverable_override": recoverable, "captured_inside_window": inside,
            # the recorder knows run_uid even where the log context is not bound
            "run_uid_hint": getattr(active[0], "run_uid", None) if active else None,
        }
        if not inside:
            # outside the window classification may read the traceback now,
            # so the stage reason carries the final code immediately
            rec["error_code"] = error_catalog.classify(exc, code, component)
        resolved = rec.get("error_code") or code
        gate.submit("error", rec)  # refused on overflow: id stays allocated
    except Exception:  # noqa: BLE001  capture never alters the run (5.2a)
        _count_failure()
        _note(active, None, code, note)  # stage still learns of the failure
        return None, code
    _note(active, rec["error_id"], resolved, note)
    return rec["error_id"], resolved


def _note(active: Any, error_id: Optional[str], code: Optional[str], enabled: bool) -> None:
    """Tell the active stage about a swallowed failure; never raises."""
    if not enabled or active is None:
        return
    try:
        active[0].note(active[1], error_id, code)
    except Exception:  # noqa: BLE001  observability is subordinate
        _count_failure()


def capture_message(message: str, code: str, component: Optional[str] = None) -> Optional[str]:
    """Capture a failure observed without an exception (for example a None result)."""
    from core.errors import AlemsError
    return capture(AlemsError(message, code=code), component=component)


def _traceback_hash(exc: BaseException) -> str:
    """Fingerprint of the stack location: frames as (relative file, function, line)."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    parts = []
    for f in frames:
        fname = f.filename[len(_ENGINE_ROOT) + 1:] if f.filename.startswith(_ENGINE_ROOT) else Path(f.filename).name
        parts.append("%s:%s:%s" % (fname, f.name, f.lineno))
    parts.append(type(exc).__module__ + "." + type(exc).__qualname__)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _free_text(exc: BaseException) -> Tuple[Optional[str], Optional[str], int]:
    """Redacted message and traceback; on redaction failure omit both (section 9)."""
    try:
        msg, n1 = redact.redact(str(exc))
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tb, n2 = redact.redact(tb)
        return msg, tb, n1 + n2
    except Exception:  # noqa: BLE001  never write unredacted text
        return None, None, -1


def _final_code(rec: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Resolved code and declared code; unknown codes fall back to GEN."""
    code = rec.get("error_code") or error_catalog.classify(
        rec["exc"], rec["declared_code"], rec["component"])
    if error_catalog.lookup(code) is None and error_catalog.load()["codes"]:
        return GENERIC_CODE, code  # validator reports unknown literals
    return code, rec["declared_code"]


def build_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an in memory capture into the persisted record (after t1 only)."""
    exc = rec["exc"]
    ctx = rec["context"]
    code, declared = _final_code(rec)
    entry = error_catalog.lookup(code) or {}
    recoverable = bool(entry.get("recoverable", False))
    if rec["recoverable_override"] is False:
        recoverable = False  # sites may only lower recoverability
    msg, tb, n = _free_text(exc)
    return {
        "record_format": RECORD_FORMAT, "error_id": rec["error_id"],
        "error_code": code, "declared_code": declared,
        "catalog_version": error_catalog.load()["catalog_version"],
        "ts": rec["ts"], "event_seq": rec["event_seq"], "component": rec["component"],
        "stage_id": rec["stage_id"], "run_uid": ctx.get("run_uid") or rec.get("run_uid_hint"), "run_id": ctx.get("run_id"),
        "sandbox_id": ctx.get("sandbox_id"), "exp_id": ctx.get("exp_id"),
        "store_path": ctx.get("store_path"), "host": ctx.get("host") or socket.gethostname(),
        "engine_id": ctx.get("engine_id"), "pid": rec["pid"],
        "exception_class": type(exc).__module__ + "." + type(exc).__qualname__,
        "message": msg, "traceback_text": tb, "traceback_hash": _traceback_hash(exc),
        "recoverable": recoverable, "captured_inside_window": rec["captured_inside_window"],
        "redactions": n,
    }


def _write_one(directory: Path, record: Dict[str, Any]) -> None:
    """Atomic write: temporary file in the same directory, then rename."""
    day = directory / record["ts"][:10]
    day.mkdir(parents=True, exist_ok=True)
    final = day / (record["error_id"] + ".json")
    tmp = day / ("." + record["error_id"] + ".tmp")
    tmp.write_text(json.dumps(record, sort_keys=True, indent=1))
    os.replace(str(tmp), str(final))


def _write_records(records: List[Dict[str, Any]]) -> None:
    """Gate consumer: persist records; a failing record is counted, others continue."""
    directory = locations.error_dir()
    for rec in records:
        try:
            if directory is None:
                raise RuntimeError("no error directory (data root not configured)")
            _write_one(directory, build_record(rec))
        except Exception as exc:  # noqa: BLE001  observability is subordinate
            _count_failure()
            logger.warning("%s error record %s not written: %s",
                           CAPTURE_FAILED_CODE, rec.get("error_id"), exc)
        finally:
            rec.pop("exc", None)  # release frames held since capture


def find(error_id: str, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Look up an error record by id.

    Searches the given directory, else this store's error directory, then the
    former host level error directory (records written before errors moved
    beside the store), so older error_ref values still resolve.
    """
    roots = [directory] if directory else [locations.error_dir()]
    if directory is None:
        roots += [p for p in locations.legacy_host_dirs() if p.name == "error"]
    for root in roots:
        if root is None or not root.exists():
            continue
        for path in root.glob("*/%s.json" % error_id):
            return json.loads(path.read_text())
    return None
