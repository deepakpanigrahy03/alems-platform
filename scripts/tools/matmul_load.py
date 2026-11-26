#!/usr/bin/env python3
"""
Fixed matmul GPU load (X1 LOAD condition; reused by the workload conditions).

Runs C = A @ B on square matrices in fixed periods: busy for duty x period,
then idle for the rest of the period, so GPU power is steady and free of
request level variation. Needs torch with CUDA, so it runs in the
interpreter named in config (x1_update_rates.load.python), not the A-LEMS venv.

Prints one summary line at exit (also on SIGTERM): iterations, busy time,
wall time and achieved duty.
"""
import argparse
import signal
import sys
import time

_STOP = False  # set by SIGTERM so the summary line is still printed


def _on_term(signum, frame):
    """Request a clean stop at the end of the current period."""
    global _STOP
    _STOP = True


def parse_args(argv=None):
    """Command line; the caller passes every value from config."""
    ap = argparse.ArgumentParser(description="Fixed duty cycle matmul load.")
    ap.add_argument("--n", type=int, required=True, help="square matrix size")
    ap.add_argument("--dtype", default="bfloat16",
                    choices=["bfloat16", "float16", "float32"])
    ap.add_argument("--duty", type=float, required=True, help="busy share of a period, 0..1")
    ap.add_argument("--period-ms", type=float, required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def validate(args):
    """Refuse values outside a safe, meaningful range."""
    if not 0.0 < args.duty <= 1.0:
        raise SystemExit("duty must be in (0, 1]")
    if not 256 <= args.n <= 16384:
        raise SystemExit("n must be in [256, 16384]")
    if args.period_ms <= 0 or args.duration_s <= 0:
        raise SystemExit("period and duration must be positive")


def run_period(a, b, c, matmul, sync, busy_s, period_s):
    """One period: matmuls until busy_s has passed, then sleep to the period end."""
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < busy_s:
        matmul(a, b, out=c)  # preallocated output: no allocator noise
        sync()  # wait for the GPU so busy time is GPU time, not launch time
        n += 1
    busy = time.perf_counter() - t0
    time.sleep(max(0.0, period_s - busy))
    return n, busy


def main(argv=None):
    """Run the load for the requested duration and print a summary line."""
    args = parse_args(argv)
    validate(args)
    import torch  # imported late so --help works without torch
    if not torch.cuda.is_available():
        print("matmul_load error: CUDA not available")
        return 2
    torch.manual_seed(args.seed)
    dt = getattr(torch, args.dtype)
    a = torch.randn(args.n, args.n, device="cuda", dtype=dt)
    b = torch.randn(args.n, args.n, device="cuda", dtype=dt)
    c = torch.empty(args.n, args.n, device="cuda", dtype=dt)
    signal.signal(signal.SIGTERM, _on_term)
    busy_s = args.duty * args.period_ms / 1000.0
    period_s = args.period_ms / 1000.0
    t_start = time.perf_counter()
    end = t_start + args.duration_s
    iters, busy = 0, 0.0
    while not _STOP and time.perf_counter() < end:
        n, bs = run_period(a, b, c, torch.matmul, torch.cuda.synchronize, busy_s, period_s)
        iters, busy = iters + n, busy + bs
    wall = time.perf_counter() - t_start
    print("matmul_load n=%d dtype=%s iters=%d busy_s=%.2f wall_s=%.2f duty=%.3f"
          % (args.n, args.dtype, iters, busy, wall, busy / wall if wall else 0.0))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
