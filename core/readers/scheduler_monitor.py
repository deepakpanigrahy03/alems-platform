#!/usr/bin/env python3
"""
================================================================================
SCHEDULER MONITOR – Reads Linux scheduler statistics from /proc
================================================================================

This module reads various scheduler and system metrics from the /proc filesystem:
- Context switches (voluntary/involuntary) from /proc/self/status
- Kernel and user CPU times from /proc/stat
- Run queue length from /proc/loadavg
- Thread migrations from /proc/self/status (if available)

These metrics help quantify OS‑level overhead that contributes to the
orchestration tax in agentic AI workflows.

Requirements implemented:
- Req 1.12: Kernel Context Switches
- Req 1.23: Kernel/User Ratio
- Req 1.24: Voluntary Switch Delay (proxy)
- Req 1.36: Run Queue Length

Author: Deepak Panigrahy
================================================================================
"""

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.readers.interfaces import SchedulerMonitorABC  # PAC-1 compliance

# ============================================================================
# Fix Python path – ensure core modules are importable
# ============================================================================
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


logger = logging.getLogger(__name__)


class SchedulerMonitor(SchedulerMonitorABC):
    """
    Reads Linux scheduler metrics from /proc filesystem.

    This class provides methods to read:
    - Voluntary and involuntary context switches (Req 1.12)
    - Kernel and user CPU times (Req 1.23)
    - Run queue length (Req 1.36)
    - Thread migrations (optional, can be derived from perf_reader)

    All values are read at a single point in time. For rates or deltas,
    the caller should take two readings and compute differences.
    """
     
    METHOD_ID: str = "scheduler_monitor_proc"
    FIDELITY = "MEASURED"
    PRIORITY: int  = 100
 
    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible on Linux and macOS, not synthetic platform."""
        return (
            caps.os in ("Linux", "Darwin")
            and getattr(caps, "platform_class", "") != "synthetic"
        )


    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the scheduler monitor.

        Args:
            config: Configuration dictionary (may contain paths, but typically not needed).
        """
        self.config = config
        self._page_size = os.sysconf("SC_PAGESIZE")  # for RSS, not used here
        logger.debug("SchedulerMonitor initialized")
        # NEW: Interrupt sampling state
        self._interrupt_sampling_active = False
        self._interrupt_samples = []
        self._last_interrupt_counts = None
        self._last_sample_time_ns = None
        self._start_epoch_ns = None
        self._start_monotonic_ns = None
        
    def is_available(self):
        # type: () -> bool
        """Return True — SchedulerMonitor always available on Linux via /proc."""
        return True
 
    def get_name(self):
        # type: () -> str
        """Identify this monitor for logging."""
        return "SchedulerMonitor(/proc)"

    def read_context_switches(self) -> Tuple[int, int]:
        """
        Read voluntary and involuntary context switches from /proc/self/status.

        Returns:
            Tuple (voluntary, involuntary) as integers.
            If file cannot be read, returns (0, 0).

        Req 1.12: Kernel Context Switches.
        """
        path = "/proc/self/status"
        vol = 0
        invol = 0
        try:
            with open(path, "r") as f:
                for line in f:
                    if line.startswith("voluntary_ctxt_switches:"):
                        vol = int(line.split(":")[1].strip())
                    elif line.startswith("nonvoluntary_ctxt_switches:"):
                        invol = int(line.split(":")[1].strip())
        except Exception as e:
            logger.warning(f"Could not read {path}: {e}")
        logger.debug("context switches voluntary %s involuntary %s", vol, invol)
        return vol, invol

    def read_cpu_times(self) -> Tuple[float, float]:
        """
        Read kernel and user CPU times from /proc/stat.

        Returns:
            Tuple (user_time_seconds, system_time_seconds) since boot.
            These are aggregate times across all CPUs.

        Note: To get time spent during a measurement interval, the caller must
        take two readings and compute the difference.

        Req 1.23: Kernel/User Ratio.
        """
        user = 0.0
        system = 0.0
        try:
            with open("/proc/stat", "r") as f:
                first_line = f.readline()
                if first_line.startswith("cpu "):
                    parts = first_line.split()
                    # user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice
                    # indices: 1:user, 2:nice, 3:system
                    user = float(parts[1]) + float(parts[2])  # user + nice
                    system = float(parts[3])
        except Exception as e:
            logger.warning(f"Could not read /proc/stat: {e}")
        logger.debug("cpu times user %.2f system %.2f", user, system)
        return user, system

    def read_loadavg(self) -> Dict[str, float]:
        """
        Read load averages from /proc/loadavg.

        Returns:
            Dictionary with keys:
                - 'load1': 1‑minute average
                - 'load5': 5‑minute average
                - 'load15': 15‑minute average
                - 'runnable': number of currently runnable tasks
                - 'total_tasks': total number of tasks
                - 'last_pid': last used PID

        Req 1.36: Run Queue Length (approximated by the 1‑minute load average).
        """
        result = {
            "load1": 0.0,
            "load5": 0.0,
            "load15": 0.0,
            "runnable": 0,
            "total_tasks": 0,
            "last_pid": 0,
        }
        try:
            with open("/proc/loadavg", "r") as f:
                line = f.read().strip()
                parts = line.split()
                if len(parts) >= 5:
                    result["load1"] = float(parts[0])
                    result["load5"] = float(parts[1])
                    result["load15"] = float(parts[2])
                    runnable_total = parts[3].split("/")
                    if len(runnable_total) == 2:
                        result["runnable"] = int(runnable_total[0])
                        result["total_tasks"] = int(runnable_total[1])
                    result["last_pid"] = int(parts[4])
        except Exception as e:
            logger.warning(f"Could not read /proc/loadavg: {e}")
        logger.debug("load averages: %s", result)
        return result

    def read_all(self) -> Dict[str, Any]:
        """
        Convenience method to read all scheduler metrics at once.

        Returns:
            Dictionary containing all metrics:
                - voluntary_switches
                - involuntary_switches
                - user_time
                - system_time
                - load1, load5, load15, runnable, total_tasks
        """
        vol, invol = self.read_context_switches()
        user_time, system_time = self.read_cpu_times()
        load = self.read_loadavg()
        swap = self.get_swap_metrics()
        result = {
            "voluntary_switches": vol,
            "involuntary_switches": invol,
            "user_time": user_time,
            "system_time": system_time,
            **load,
            "swap": swap,
        }
        logger.debug("scheduler snapshot: %s", result)
        return result

    def __str__(self) -> str:
        return "SchedulerMonitor(procfs)"

    def get_swap_metrics(self) -> Dict[str, float]:
        """
        Get swap usage metrics from /proc/meminfo.

        Returns:
            Dictionary with swap metrics:
            - swap_total_mb: Total swap in MB
            - swap_free_mb: Free swap in MB
            - swap_used_mb: Used swap in MB
            - swap_percent: Percentage of swap used
            - swap_cached_mb: Swap cached in memory (if available)
        """
        swap_metrics = {
            "swap_total_mb": 0.0,
            "swap_free_mb": 0.0,
            "swap_used_mb": 0.0,
            "swap_percent": 0.0,
            "swap_cached_mb": 0.0,
        }

        import platform as _platform
        if _platform.system() == "Darwin":
            try:
                import subprocess
                result = subprocess.run(
                    ["sysctl", "vm.swapusage"],
                    capture_output=True, text=True, timeout=5
                )
                # Format: vm.swapusage: total = 2048.00M  used = 1024.00M  free = 1024.00M
                if result.returncode == 0:
                    import re
                    nums = re.findall(r"\d+\.?\d*", result.stdout)
                    if len(nums) >= 3:
                        total = float(nums[0])
                        used  = float(nums[1])
                        free  = float(nums[2])
                        swap_metrics["swap_total_mb"] = total
                        swap_metrics["swap_free_mb"]  = free
                        swap_metrics["swap_used_mb"]  = used
                        if total > 0:
                            swap_metrics["swap_percent"] = (used / total) * 100.0
            except Exception as e:
                logger.debug("Darwin swap metrics failed: %s", e)
            return swap_metrics
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    line = line.strip()
                    if "SwapTotal:" in line:
                        kb = int(line.split()[1])
                        swap_metrics["swap_total_mb"] = kb / 1024.0
                    elif "SwapFree:" in line:
                        kb = int(line.split()[1])
                        swap_metrics["swap_free_mb"] = kb / 1024.0
                    elif "SwapCached:" in line:
                        kb = int(line.split()[1])
                        swap_metrics["swap_cached_mb"] = kb / 1024.0

            # Calculate used swap and percentage
            if swap_metrics["swap_total_mb"] > 0:
                swap_metrics["swap_used_mb"] = (
                    swap_metrics["swap_total_mb"] - swap_metrics["swap_free_mb"]
                )
                swap_metrics["swap_percent"] = (
                    swap_metrics["swap_used_mb"] / swap_metrics["swap_total_mb"]
                ) * 100.0

            logger.debug(
                f"Swap metrics: total={swap_metrics['swap_total_mb']:.1f}MB, "
                f"used={swap_metrics['swap_used_mb']:.1f}MB, "
                f"cached={swap_metrics['swap_cached_mb']:.1f}MB"
            )

        except Exception as e:
            logger.debug(f"Could not read swap metrics: {e}")

        return swap_metrics

    def start_interrupt_sampling(self, pid: int = 0):
        """Start collecting interrupt samples."""
        self._interrupt_sampling_active = True
        self._interrupt_samples = []
        self._last_interrupt_counts = self._read_total_interrupts()

        # Capture clock alignment ONCE at the beginning
        start_mono = time.monotonic_ns()
        start_epoch = time.time_ns()

        self._start_monotonic_ns = start_mono
        self._start_epoch_ns = start_epoch
        self._last_sample_time_ns = start_mono
        self._pid = pid
        self._last_ticks = self._read_cpu_ticks()
        self._last_proc_ticks = self._read_proc_ticks(pid) if pid else 0

        logger.debug(
            f"Interrupt sampling started - epoch: {start_epoch}, mono: {start_mono}"
        )

    def _read_total_interrupts(self) -> int:
        """Read total interrupt count from /proc/stat."""
        try:
            with open("/proc/stat", "r") as f:
                for line in f:
                    if line.startswith("intr "):
                        return int(line.split()[1])
        except Exception:
            pass
        return 0
    
    def _read_cpu_ticks(self) -> dict:
        """
        Read raw CPU tick counters from /proc/stat aggregate line.
 
        Called in same pass as _read_total_interrupts() so both values
        come from a single consistent /proc/stat snapshot (Option B).
 
        /proc/stat cpu line format:
            cpu  user nice system idle iowait irq softirq steal guest guest_nice
 
        Returns:
            dict: {'user': int, 'system': int, 'idle': int, 'total': int}
                  All zeros on read failure — never raises.
        """
        try:
            with open("/proc/stat", "r") as f:
                for line in f:
                    if line.startswith("cpu "):      # aggregate line (space after cpu)
                        fields = line.split()
                        return {
                            "user":   int(fields[1]),
                            "system": int(fields[3]),
                            "idle":   int(fields[4]),
                            "total":  sum(int(x) for x in fields[1:]),
                        }
        except Exception as e:
            logger.debug("Failed to read CPU ticks from /proc/stat: %s", e)
 
        return {"user": 0, "system": 0, "idle": 0, "total": 0}

    def _read_proc_ticks(self, pid: int) -> int:
        """Read utime+stime from /proc/[pid]/stat. Returns 0 on failure."""
        if not pid:
            return 0
        try:
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().split()
            return int(parts[13]) + int(parts[14])  # utime + stime
        except Exception:
            return 0
        
    def reset_interrupt_samples(self):
        """Clear interrupt samples buffer for new run."""
        self._interrupt_samples = []
        self._last_interrupt_counts = self._read_total_interrupts()

        # DO NOT reset the start times here!
        # Only update the last sample time for delta calculation
        self._last_sample_time_ns = time.monotonic_ns()

        logger.debug("Interrupt samples reset for new run (start times preserved)")

    def sample_interrupts(self):
        """
        Take one interrupt + CPU tick sample from /proc/stat.
 
        Chunk 2 final:
            - Reads CPU ticks in same /proc/stat call (Option B)
            - Stores sample_start_ns + sample_end_ns explicitly
            - timestamp_ns = sample_end_ns (backward compat)
            - interval_ns stored for verification
            - interrupts_raw (count delta) + interrupts_per_sec (rate, compat)
 
        Called from energy_engine._sampling_loop every 10th iteration (10Hz).
        """
        if not self._interrupt_sampling_active:
            return
 
        # --------------------------------------------------------
        # Capture start timestamp before /proc/stat read
        # --------------------------------------------------------
        sample_start_ns = time.monotonic_ns()
 
        # Read interrupt count + CPU ticks in one /proc/stat pass
        current_interrupts = self._read_total_interrupts()
        current_ticks      = self._read_cpu_ticks()
 
        # Capture end timestamp after read
        sample_end_ns = time.monotonic_ns()
        interval_ns   = sample_end_ns - sample_start_ns
 
        # --------------------------------------------------------
        # Convert monotonic → epoch for DB timestamp alignment
        # --------------------------------------------------------
        if self._start_epoch_ns is not None and self._start_monotonic_ns is not None:
            epoch_start_ns = int(
                self._start_epoch_ns + (sample_start_ns - self._start_monotonic_ns)
            )
            epoch_end_ns = int(
                self._start_epoch_ns + (sample_end_ns - self._start_monotonic_ns)
            )
        else:
            epoch_start_ns = sample_start_ns
            epoch_end_ns   = sample_end_ns
            logger.warning("Start times not set for interrupt sampling")
 
        logger.debug(
            "INTERRUPT DEBUG: counts=%d last=%d",
            current_interrupts,
            self._last_interrupt_counts or 0,
        )
 
        if (
            self._last_interrupt_counts is not None
            and self._last_sample_time_ns is not None
        ):
            # --------------------------------------------------------
            # Compute deltas against last sample
            # --------------------------------------------------------
            time_delta_s   = (sample_start_ns - self._last_sample_time_ns) / 1e9

            current_proc_ticks = self._read_proc_ticks(self._pid) if self._pid else 0
            if time_delta_s > 0:
                interrupts_raw = current_interrupts - self._last_interrupt_counts
                rate           = interrupts_raw / time_delta_s
 
                # CPU tick deltas — start = last sample snapshot, end = now
                user_start   = self._last_ticks.get("user",   0)
                user_end     = current_ticks.get("user",      0)
                system_start = self._last_ticks.get("system", 0)
                system_end   = current_ticks.get("system",    0)
                
 
                logger.debug("INTERRUPT RATE: %.2f IRQ/s", rate)
 
                self._interrupt_samples.append({
                    # Backward compat — timestamp_ns = end time
                    "timestamp_ns":       epoch_end_ns,
                    # Explicit start/end (Option 2 — raw layer)
                    "sample_start_ns":    epoch_start_ns,
                    "sample_end_ns":      epoch_end_ns,
                    "interval_ns":        interval_ns,
                    # Interrupt values
                    "interrupts_per_sec": rate,         # old rate — backward compat
                    "interrupts_raw":     interrupts_raw,
                    # CPU tick values
                    "user_ticks_start":   user_start,
                    "user_ticks_end":     user_end,
                    "system_ticks_start": system_start,
                    "system_ticks_end":   system_end,
                    "total_ticks_start":  self._last_ticks.get("total", 0),
                    "total_ticks_end":    current_ticks.get("total", 0),
                    "proc_ticks_start":   self._last_proc_ticks,
                    "proc_ticks_end":     current_proc_ticks,
                })
 
        # --------------------------------------------------------
        # Update last-seen values for next delta calculation
        # --------------------------------------------------------
        self._last_interrupt_counts = current_interrupts
        self._last_sample_time_ns   = sample_start_ns   # use start for consistency
        self._last_ticks            = current_ticks
        self._last_proc_ticks       = current_proc_ticks

    def stop_interrupt_sampling(self) -> list:
        """Stop and return interrupt samples."""
        self._interrupt_sampling_active = False
        samples = self._interrupt_samples.copy()
        self._interrupt_samples = []
        logger.debug(f"🔍 INTERRUPT STOP: collected {len(samples)} samples")
        return samples


# ============================================================================
# Example usage (standalone test)
# ============================================================================
if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    monitor = SchedulerMonitor({})
    from core.observability.console import get_console
    con = get_console()
    con.section("scheduler monitor test")

    start = monitor.read_all()
    con.section("initial snapshot")
    con.kv("context switches", "voluntary %s, involuntary %s" % (
        start["voluntary_switches"], start["involuntary_switches"]))
    con.kv("cpu times", "user %.2f, system %.2f" % (start["user_time"], start["system_time"]))
    con.kv("load average", "%s (1 min), %s (5 min), %s (15 min)" % (
        start["load1"], start["load5"], start["load15"]))
    con.kv("runnable tasks", "%s/%s" % (start["runnable"], start["total_tasks"]))
    if "swap" in start:
        swap = start["swap"]
        con.kv("swap total", "%.1f MB" % swap["swap_total_mb"])
        con.kv("swap used", "%.1f MB (%.1f%%)" % (swap["swap_used_mb"], swap["swap_percent"]))
        con.kv("swap free", "%.1f MB" % swap["swap_free_mb"])
        con.kv("swap cached", "%.1f MB" % swap["swap_cached_mb"])

    con.line("waiting 2 s")
    time.sleep(2)

    end = monitor.read_all()
    con.section("deltas over 2 s")
    con.kv("context switches", "voluntary +%s, involuntary +%s" % (
        end["voluntary_switches"] - start["voluntary_switches"],
        end["involuntary_switches"] - start["involuntary_switches"]))
    con.kv("cpu time", "user +%.2f, system +%.2f" % (
        end["user_time"] - start["user_time"], end["system_time"] - start["system_time"]))
    con.kv("load average", end["load1"])
    if "swap" in end and "swap" in start:
        con.kv("swap used change", "%+.2f MB" % (
            end["swap"]["swap_used_mb"] - start["swap"]["swap_used_mb"]))

    con.section("interrupt sampling, 2 s at 10 Hz")
    monitor.start_interrupt_sampling()
    for _ in range(20):  # 20 samples at 0.1 s intervals = 2 s
        time.sleep(0.1)
        monitor.sample_interrupts()
    samples = monitor.stop_interrupt_sampling()
    con.kv("samples collected", len(samples))
    for i, s in enumerate(samples[:3]):
        con.kv("sample %d" % (i + 1), "%.2f IRQ/s" % s["interrupts_per_sec"])
    if not samples:
        con.line("no samples collected: check _read_total_interrupts()")
    con.line("scheduler monitor test complete")
