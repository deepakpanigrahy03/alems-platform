"""
engine_provenance.py: map an experiment to the serving instance that answered it.

Why this exists:
    A model name alone does not identify a server. Two servers can serve the
    same model on two ports, and one server can be restarted with new flags.
    This module finds the process LISTENING on the provider port, takes its
    PID and process start time, and matches them to the provenance JSON that
    the ai-stack serve scripts write at every start (schema
    ai_stack_provenance_v2). It never picks "the newest JSON for the model".

    The listener may be an API process only (vLLM runs GPU work in a child
    EngineCore). Child processes are recorded with an association status:
      confirmed  a child matches the engine's GPU worker pattern, or the engine
                 is single process (llama.cpp) and the listener is the worker
      inferred   children exist but none matches a known worker pattern
      unknown    no children found for a multi process engine, or lookup failed
    Analyses that need process mapping must use status confirmed only.

Method: engine_instance_match_v1 (SYSTEM). Never raises: every failure is
logged and becomes NULL fields plus a match_method that says why (DC-3).
"""

import datetime
import glob
import hashlib
import json
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

METHOD_ID = "engine_instance_match_v1"
PROVENANCE_SCHEMA = "ai_stack_provenance_v2"
# Where serve scripts write provenance JSON; same variable the scripts read.
SERVE_LOG_DIR_ENV = "ALEMS_SERVE_LOG_DIR"
SERVE_LOG_DIR_DEFAULT = "/opt/ai-stack/logs"
# PID start time in JSON (from /proc) and psutil create_time agree to ~10 ms;
# 1 s tolerance absorbs clock tick rounding but rejects a reused PID.
START_TOLERANCE_S = 1.0
# Hosts that mean "a server on this machine" (remote servers are not matched).
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
# Child process names or command lines that do the GPU work, per engine.
WORKER_PATTERNS = {
    "vllm": re.compile(r"VLLM::(EngineCore|Worker)"),
    "sglang": re.compile(r"sglang::(scheduler|detokenizer)|sglang\.srt"),
}
SINGLE_PROCESS_ENGINES = ("llamacpp",)
# Same redaction rules as the ai-stack writer (command of an unmatched listener).
SECRET_FLAGS = {"--api-key", "--admin-api-key", "--hf-token", "--token",
                "--api_key", "--hf_token", "--password", "--secret"}
SECRET_VALUE = re.compile(r"^(hf_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_\-]{16,}|nvapi-[A-Za-z0-9_\-]{16,})$")

# Cache: one listener lookup result per (port, pid, start) so repeated runs of
# one experiment do not rescan the log directory.
_CACHE = {}  # type: Dict[Tuple[int, int, float], Dict[str, Any]]


def port_of_local_url(base_url):
    # type: (Optional[str]) -> Optional[int]
    """
    Port of a base URL that points at this machine.

    Args:
        base_url: provider base URL, e.g. http://localhost:8000/v1.

    Returns:
        Port number, or None for a remote, empty or unparsable URL.
    """
    if not base_url:
        return None
    try:
        url = urlparse(base_url)
    except ValueError as exc:
        logger.warning("engine provenance: bad base_url %r: %s", base_url, exc)
        return None
    if url.hostname not in LOCAL_HOSTS:
        return None   # remote server: process tree not visible from here
    return url.port or (443 if url.scheme == "https" else 80)


def base_url_for_provider(provider):
    # type: (Optional[str]) -> Optional[str]
    """
    base_url of a provider from the same models config ModelFactory reads.

    Returns None (logged) when the provider or the loader is unavailable, so
    the run is stored without a provenance row rather than failing.
    """
    if not provider:
        return None
    try:
        from core.execution import model_factory  # same loader, same sandbox config
        block = model_factory._loader._load()["providers"].get(provider) or {}
    except Exception as exc:
        logger.warning("engine provenance: provider %s not resolved: %s", provider, exc)
        return None
    meta = block.get("provider_meta") or block
    return os.path.expandvars(meta.get("base_url") or "") or None


def redact(cmd):
    # type: (List[str]) -> List[str]
    """Copy of an argv list with secret values replaced by <redacted>."""
    out, skip = [], False
    for arg in cmd:
        flag, sep, _ = arg.partition("=")
        if skip or SECRET_VALUE.match(arg):
            out.append("<redacted>")
            skip = False
            continue
        if flag.lower() in SECRET_FLAGS:
            # "--api-key=x" keeps the flag; "--api-key x" redacts the next item
            out.append(flag + "=<redacted>" if sep else arg)
            skip = not sep
            continue
        out.append(arg)
    return out


def _utc_iso(epoch_s):
    # type: (Optional[float]) -> Optional[str]
    """Epoch seconds to ISO UTC with ms, as the provenance JSON writes it."""
    if epoch_s is None:
        return None
    ts = datetime.datetime.fromtimestamp(epoch_s, datetime.timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def find_listener(port):
    # type: (int) -> Optional[Any]
    """
    psutil.Process listening on TCP port, or None.

    Returns None (logged) when psutil is missing, access is denied, or no
    process listens; the caller records listener_identified = 0.
    """
    try:
        import psutil
        conns = psutil.net_connections(kind="tcp")
    except Exception as exc:  # ImportError or AccessDenied: record, not raise
        logger.warning("engine provenance: listener scan failed: %s", exc)
        return None
    pids = {c.pid for c in conns
            if c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.pid}
    if len(pids) != 1:
        logger.info("engine provenance: port %s listeners %s", port, sorted(pids))
        return None   # none, or ambiguous: never guess
    return psutil.Process(pids.pop())


def find_record(pid, start_s, log_dir):
    # type: (int, float, str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]
    """
    Provenance JSON whose pid and process start time match the listener.

    Returns:
        (record, path), or (None, None) when no v2 record matches.
    """
    for path in sorted(glob.glob(os.path.join(log_dir, "serve_*.json")), reverse=True):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("engine provenance: unreadable %s: %s", path, exc)
            continue
        if rec.get("schema") != PROVENANCE_SCHEMA or rec.get("pid") != pid:
            continue   # older records have no pid; pid alone is not enough
        rec_start = _parse_iso(rec.get("process_start_utc"))
        if rec_start is not None and abs(rec_start - start_s) <= START_TOLERANCE_S:
            return rec, path
    return None, None


def _parse_iso(text):
    # type: (Optional[str]) -> Optional[float]
    """ISO UTC string (with or without ms, trailing Z) to epoch seconds."""
    if not text:
        return None
    try:
        ts = datetime.datetime.strptime(text.rstrip("Z")[:23], "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        return None
    return ts.replace(tzinfo=datetime.timezone.utc).timestamp()


def workers_of(proc, engine):
    # type: (Any, Optional[str]) -> Tuple[List[Dict[str, Any]], str, str]
    """
    Child processes of the listener and the association status.

    Returns:
        (workers, method, status); status in confirmed, inferred, unknown.
    """
    if engine in SINGLE_PROCESS_ENGINES:
        return [], "single_process_engine", "confirmed"
    try:
        children = proc.children(recursive=True)
        workers = [{"pid": c.pid, "start_utc": _utc_iso(c.create_time()),
                    "cmd": redact(c.cmdline()), "name": c.name()} for c in children]
    except Exception as exc:  # child exited or access denied mid scan
        logger.warning("engine provenance: worker scan failed: %s", exc)
        return [], "process_tree", "unknown"
    if not workers:
        return [], "process_tree", "unknown"
    pattern = WORKER_PATTERNS.get(engine or "")
    hit = pattern and any(pattern.search(w["name"] + " " + " ".join(w["cmd"])) for w in workers)
    return workers, "process_tree_name_match", "confirmed" if hit else "inferred"


def match_instance(base_url, log_dir=None):
    # type: (Optional[str], Optional[str]) -> Optional[Dict[str, Any]]
    """
    Provenance row for the server behind base_url, or None for a remote URL.

    Args:
        base_url: provider base URL from models.yaml (env vars expanded).
        log_dir:  provenance JSON directory; default from ALEMS_SERVE_LOG_DIR.

    Returns:
        dict with every engine_provenance column except provenance_id, exp_id.
    """
    port = port_of_local_url(base_url)
    if port is None:
        return None
    proc = find_listener(port)
    if proc is None:
        return _empty_row(port, "no_listener")
    key = (port, proc.pid, proc.create_time())
    if key not in _CACHE:
        _CACHE[key] = _build_row(port, proc, log_dir or os.environ.get(
            SERVE_LOG_DIR_ENV, SERVE_LOG_DIR_DEFAULT))
    return dict(_CACHE[key])


def _build_row(port, proc, log_dir):
    # type: (int, Any, str) -> Dict[str, Any]
    """Assemble one row from the listener process and its provenance JSON."""
    start_s = proc.create_time()
    rec, path = find_record(proc.pid, start_s, log_dir)
    rec = rec or {}
    cmd = rec.get("command") or redact(_safe(proc.cmdline, []))
    engine = rec.get("engine") or _engine_from_cmd(cmd)
    workers, w_method, w_status = workers_of(proc, engine)
    row = _empty_row(port, "pid_start_time" if path else "listener_only_no_record")
    row.update({
        "engine": engine, "engine_version": rec.get("engine_version"),
        "server_pid": proc.pid, "server_start_utc": _utc_iso(start_s),
        "model_id": rec.get("model_id"),
        "command_json": json.dumps(cmd),
        "command_sha256": hashlib.sha256("\0".join(cmd).encode()).hexdigest(),
        "source_path": path, "source_sha256": _sha256_file(path),
        "server_instance_id": hashlib.sha256(("%s|%d|%s" % (
            socket.gethostname(), proc.pid, _utc_iso(start_s))).encode()).hexdigest()[:16],
        "worker_pids_json": json.dumps(workers), "listener_identified": 1,
        "worker_identified": int(bool(workers) or w_status == "confirmed"),
        "worker_association_method": w_method, "worker_association_status": w_status,
    })
    return row


def _empty_row(port, match_method):
    # type: (int, str) -> Dict[str, Any]
    """Row with NULL facts (MIC-1) and the reason in match_method."""
    keys = ("engine", "engine_version", "server_pid", "server_start_utc", "model_id",
            "command_json", "command_sha256", "source_path", "source_sha256",
            "server_instance_id", "worker_pids_json", "worker_association_method")
    row = dict.fromkeys(keys)  # type: Dict[str, Any]
    row.update({"port": port, "match_method": match_method, "listener_identified": 0,
                "worker_identified": 0, "worker_association_status": "unknown",
                "method_id": METHOD_ID})
    return row


def _engine_from_cmd(cmd):
    # type: (List[str]) -> Optional[str]
    """Engine name from a command line when no provenance record matched."""
    text = " ".join(cmd)
    for name, needle in (("vllm", "vllm"), ("sglang", "sglang"), ("llamacpp", "llama_cpp")):
        if needle in text:
            return name
    return None


def _safe(fn, default):
    """Call fn(); default on any process error (process may have exited)."""
    try:
        return fn()
    except Exception as exc:
        logger.warning("engine provenance: process read failed: %s", exc)
        return default


def _sha256_file(path):
    # type: (Optional[str]) -> Optional[str]
    """sha256 of the matched JSON file, None when absent."""
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as exc:
        logger.warning("engine provenance: hash failed %s: %s", path, exc)
        return None
