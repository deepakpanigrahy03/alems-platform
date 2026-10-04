#!/usr/bin/env python3
"""
strace_window_check.py: prove no observability I/O inside the gate window
(39.5.2 WP 2b acceptance).

Inputs:
  strace file  from: strace -f -ttt -e trace=openat,close,socket,pipe2,eventfd2,write,pwrite64,writev,sendto,sendmsg,connect -o F <cmd>
  trace file   from: ALEMS_GATE_TRACE=<path> (one line per window: pid enter_epoch exit_epoch)

Each syscall inside a window is classified by the path behind its fd:
  observability  .jsonl files, the error dir, alems log dir   -> FAIL
  store          .db, -wal, -journal, -shm                     -> REVIEW (application or observability)
  socket         sendto, sendmsg, connect, socket fds          -> REVIEW
  pipe           pipes and eventfds (threads, subprocesses)    -> info
  stdio          fd 1 and 2 (prints, 2e scope)                 -> evidence only
  other          everything else                               -> info

Exit codes (C-CLI): 0 pass, 2 usage, 4 FAIL found.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

# "PID EPOCH syscall(args) = ret" ; -f prefixes the pid.
LINE = re.compile(r"^(\d+)\s+(\d+\.\d+)\s+(\w+)\((.*)\)\s+=\s+(-?\d+)")
OPEN_PATH = re.compile(r'"([^"]+)"')
FIRST_FD = re.compile(r"^(\d+)")


def load_windows(path: str) -> List[Tuple[float, float]]:
    """Read gate intervals; pid is ignored because -f spans all threads."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 3:
                out.append((float(parts[1]), float(parts[2])))
    return out


def classify(syscall: str, fd: int, path: str) -> str:
    """Map one write side syscall to a class."""
    if syscall in ("sendto", "sendmsg", "connect") or path.startswith("socket"):
        return "socket"
    if path in ("pipe", "eventfd"):
        return "pipe"
    if fd in (1, 2):
        return "stdio"
    if path.endswith(".jsonl") or "/error/" in path or "/log/" in path:
        return "observability"
    if path.endswith((".db", "-wal", "-journal", "-shm")):
        return "store"
    return "other"


def inside(ts: float, windows: List[Tuple[float, float]]) -> bool:
    """True when ts falls in any window."""
    return any(a <= ts <= b for a, b in windows)


def scan(strace_path: str, windows: List[Tuple[float, float]]) -> Dict[str, Counter]:
    """
    Walk the strace file once, tracking fd to path.

    Keyed by fd only: strace -f labels lines with thread ids, and threads share
    one fd table, so a per id map misses fds opened by another thread.
    """
    fdmap: Dict[Tuple[str, int], str] = {}
    found: Dict[str, Counter] = defaultdict(Counter)
    with open(strace_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LINE.match(line)
            if not m:
                continue
            _tid, ts, call, args, ret = m.group(1), float(m.group(2)), m.group(3), m.group(4), int(m.group(5))
            if call == "close":
                f = FIRST_FD.match(args)
                if f:
                    fdmap.pop(("", int(f.group(1))), None)
                continue
            if call in ("socket", "pipe2", "eventfd2") and ret >= 0:
                # pipe2 returns 0 and writes both fds into [r, w]
                if call == "pipe2":
                    for fd_s in re.findall(r"\d+", args.split("]")[0]):
                        fdmap[("", int(fd_s))] = "pipe"
                else:
                    kind = "socket " + args.split(",")[0] if call == "socket" else "eventfd"
                    fdmap[("", ret)] = kind
                continue
            if call == "openat":
                p = OPEN_PATH.search(args)
                if p and ret >= 0:
                    fdmap[("", ret)] = p.group(1)
                writes = "O_WRONLY" in args or "O_RDWR" in args or "O_CREAT" in args
                if p and writes and inside(ts, windows):
                    found[classify("write", -1, p.group(1))]["open " + p.group(1)] += 1
                continue
            if not inside(ts, windows):
                continue
            f = FIRST_FD.match(args)
            fd = int(f.group(1)) if f else -1
            path = fdmap.get(("", fd), "fd%d" % fd)
            found[classify(call, fd, path)][call + " " + path] += 1
    return found


def main() -> int:
    """Run the check and print a short report."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("strace_file")
    ap.add_argument("trace_file")
    a = ap.parse_args()
    windows = load_windows(a.trace_file)
    if not windows:
        print("no gate windows recorded; set ALEMS_GATE_TRACE", file=sys.stderr)
        return 2
    found = scan(a.strace_file, windows)
    print("windows: %d" % len(windows))
    for cls in ("observability", "store", "socket", "pipe", "stdio", "other"):
        total = sum(found[cls].values())
        print("%-14s %d" % (cls, total))
        for key, n in found[cls].most_common(10):
            print("    %6d  %s" % (n, key))
    return 4 if found["observability"] else 0


if __name__ == "__main__":
    sys.exit(main())
