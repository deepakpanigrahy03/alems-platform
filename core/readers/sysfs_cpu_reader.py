"""
SysfsCPUReader — per interval CPU telemetry from procfs and sysfs.

Purpose:
    Populate cpu_samples on Linux machines where turbostat is
    nonfunctional (AMD Zen 2 dies with SIGABRT in rapl_perf_init).
    Emits the SAME per sample dict keys as TurbostatReader so that
    every downstream consumer (samples.py insert_cpu_samples,
    CPUIdleRepository.write_from_turbostat) works unchanged.

Sources (all standard Linux, no root needed after fix_permissions.sh):
    utilization  — /proc/stat aggregate jiffie deltas
    frequency    — /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq
    idle states  — /sys/devices/system/cpu/cpu*/cpuidle/state*/time (usec)
    pkg power    — /sys/class/powercap/intel-rapl:0/energy_uj (AMD uses
                   the intel-rapl powercap namespace too)
    pkg temp     — /sys/class/hwmon/hwmon*/temp1_input where name=k10temp
                   (falls back to any coretemp/cpu hwmon)

Method: cpu_sysfs_sampler, MEASURED, layer=os, confidence 0.85.
Dispatch: core/readers/factory.py get_turbostat_reader() selects this
reader when the turbostat probe fails (PAC-2 — no platform imports here).

Compliance: PAC-4 (never raises, returns empty on failure),
DC-1/2/3/4 (comments, docstrings, logged failures, early returns).
"""

import glob
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# ADAPT-B (VERIFY-2): if callers of read_metrics() require a PowerState
# object, mirror TurbostatReader's import here, e.g.:
#   from core.readers.interfaces import PowerState
# Harness flow uses start/stop monitoring only, so read_metrics returns
# a plain dict by default and this import stays optional.

# Residency scale: 1.0 emits fractions in [0, 1]; set to 100.0 if
# VERIFY-3 shows write_from_turbostat expects percent like turbostat's
# CPU%c1 columns. One constant, one place to flip.
RESIDENCY_SCALE = 1.0

# Idle state names that map to dedicated cpu_samples columns. Anything
# else (POLL, vendor states) goes to extra_metrics_json — never dropped.
_COLUMN_STATES = {"C1", "C2", "C3", "C6", "C7"}

_SAMPLE_INTERVAL_S = 0.1  # 10 Hz — matches turbostat and gpu_collector


class SysfsCPUReader:
    """
    Threaded 10 Hz sampler producing turbostat compatible cpu_samples
    dicts from procfs/sysfs counters.

    Lifecycle mirrors TurbostatReader:
        r = SysfsCPUReader(config)
        r.start_monitoring()
        ... experiment runs ...
        result = r.stop_monitoring()   # {"cpu_samples": [ {...}, ... ]}
    """

    def __init__(self, config=None):
        """
        Discover all counter paths once. Discovery failures downgrade
        individual metrics to None, never the whole reader (PAC-4).

        Args:
            config: platform config dict (accepted for factory signature
                    parity, currently unused).
        """
        self.config = config or {}
        self._samples = []
        self._thread = None
        self._stop_event = threading.Event()
        self._monitoring_active = False
        # One warning per failing source per run — not one per tick.
        self._warned = set()

        self._cpu_dirs = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*"))
        self._idle_paths = self._discover_idle_paths()
        self._freq_paths = self._discover_freq_paths()
        self._rapl = self._discover_rapl()
        self._temp_path = self._discover_temp_path()

        # Available when the two core sources exist. Frequency, power
        # and temperature are enrichments and may be absent.
        self.available = bool(self._idle_paths) and os.path.exists("/proc/stat")
        logger.info(
            "SysfsCPUReader: cpus=%d idle_states=%s freq=%s rapl=%s temp=%s",
            len(self._cpu_dirs), sorted(self._idle_paths.keys()),
            bool(self._freq_paths), bool(self._rapl), bool(self._temp_path),
        )

    # ------------------------------------------------------------------
    # Discovery (init time only)
    # ------------------------------------------------------------------

    def _discover_idle_paths(self):
        """
        Map state name -> list of per cpu time files (cumulative usec).

        Returns:
            dict[str, list[str]]: e.g. {"C1": [".../state1/time", ...]}
        """
        paths = {}
        for cpu_dir in self._cpu_dirs:
            for state_dir in sorted(glob.glob(cpu_dir + "/cpuidle/state*")):
                name = self._read_str(state_dir + "/name")
                time_file = state_dir + "/time"
                if name is None or not os.path.exists(time_file):
                    continue
                paths.setdefault(name, []).append(time_file)
        return paths

    def _discover_freq_paths(self):
        """Return per cpu scaling_cur_freq files (kHz), possibly empty."""
        return [
            p for p in (d + "/cpufreq/scaling_cur_freq" for d in self._cpu_dirs)
            if os.path.exists(p)
        ]

    def _discover_rapl(self):
        """
        Locate the RAPL package domain energy counter.

        Returns:
            dict with energy_uj path and wrap range, or None. AMD
            exposes package energy under the intel-rapl powercap
            namespace; subdomains (dram) are absent on Ryzen 5 3600,
            which is why dram_power stays None by hardware truth.
        """
        for base in sorted(glob.glob("/sys/class/powercap/intel-rapl:*")):
            name = self._read_str(base + "/name") or ""
            if not name.startswith("package"):
                continue
            energy = base + "/energy_uj"
            if not os.path.exists(energy):
                continue
            max_range = self._read_int(base + "/max_energy_range_uj")
            return {"energy_uj": energy, "max_range": max_range or 0}
        return None

    def _discover_temp_path(self):
        """
        Find the CPU package temperature input. Prefers k10temp (AMD),
        falls back to coretemp (Intel) so this reader stays generic.
        """
        preferred, fallback = None, None
        for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*/")):
            name = self._read_str(hwmon_dir + "name") or ""
            temp = hwmon_dir + "temp1_input"
            if not os.path.exists(temp):
                continue
            if name == "k10temp":
                preferred = temp
            elif name == "coretemp":
                fallback = temp
        return preferred or fallback

    # ------------------------------------------------------------------
    # Lifecycle (TurbostatReader compatible surface)
    # ------------------------------------------------------------------

    def is_available(self):
        """True when /proc/stat and at least one cpuidle state exist."""
        return self.available

    def get_name(self):
        """Method id — must match seed_methodology and METHOD_CONFIDENCE."""
        return "cpu_sysfs_sampler"

    def start_monitoring(self):
        """
        Start the 10 Hz sampling thread. Idempotent; a second call while
        active is a logged no op (DC-3, no silent surprises).
        """
        if not self.available:
            logger.warning("SysfsCPUReader: not available, cpu_samples will be empty")
            return
        if self._monitoring_active:
            logger.warning("SysfsCPUReader: start_monitoring called while active")
            return
        self._samples = []
        self._warned = set()
        self._stop_event.clear()
        self._monitoring_active = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop_monitoring(self):
        """
        Stop sampling and return collected samples.

        Returns:
            dict: {"cpu_samples": list[dict]} — ADAPT-A (VERIFY-1):
            mirror TurbostatReader.stop_monitoring's exact return shape;
            adjust this dict's keys if turbostat returns more.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._monitoring_active = False
        logger.info("SysfsCPUReader: collected %d samples", len(self._samples))
        return {"cpu_samples": list(self._samples)}

    def read_metrics(self, duration_ms=100):
        """
        Synchronous one shot sample over duration_ms, for interface
        parity with TurbostatReader.read_metrics.

        Returns:
            dict: one sample dict (see ADAPT-B if a PowerState object
            is required by any caller — harness flow does not use this).
        """
        if not self.available:
            return {}
        before = self._snapshot()
        time.sleep(max(duration_ms, 10) / 1000.0)
        return self._delta_sample(before, self._snapshot())

    # ------------------------------------------------------------------
    # Sampling internals
    # ------------------------------------------------------------------

    def _sample_loop(self):
        """
        Sample at fixed cadence. Each iteration snapshots counters and
        emits the delta versus the previous snapshot. The loop must
        never raise (PAC-4): a failed tick is logged once and skipped.
        """
        prev = self._snapshot()
        while not self._stop_event.wait(_SAMPLE_INTERVAL_S):
            try:
                cur = self._snapshot()
                self._samples.append(self._delta_sample(prev, cur))
                prev = cur
            except Exception as e:
                self._warn_once("tick", "sample tick failed: %s" % e)

    def _snapshot(self):
        """
        Read all cumulative counters at one instant.

        Returns:
            dict: t_ns, proc_stat jiffies, idle usec per state,
            rapl uj, freq khz list, temp mc. Missing sources are None.
        """
        snap = {"t_ns": time.time_ns()}
        snap["stat"] = self._read_proc_stat()
        snap["idle"] = {
            state: self._sum_files(files, "idle:" + state)
            for state, files in self._idle_paths.items()
        }
        snap["rapl_uj"] = (
            self._read_int(self._rapl["energy_uj"]) if self._rapl else None
        )
        snap["freq_khz"] = [
            v for v in (self._read_int(p) for p in self._freq_paths)
            if v is not None
        ]
        snap["temp_mc"] = self._read_int(self._temp_path) if self._temp_path else None
        return snap

    def _delta_sample(self, before, after):
        """
        Convert two snapshots into one turbostat compatible sample dict.

        Key semantics match cpu_samples columns:
            cpu_util_percent — busy share of jiffies, 0 to 100
            cpu_busy_mhz     — mean governor frequency (busy freq proxy)
            cpu_avg_mhz      — busy_mhz scaled by utilization (Avg_MHz)
            c*_residency     — idle time share per interval, RESIDENCY_SCALE
            package_power    — RAPL delta energy over wall interval, watts
            package_temp     — degrees C
        """
        interval_ns = max(after["t_ns"] - before["t_ns"], 1)
        interval_s = interval_ns / 1e9
        n_cpus = max(len(self._cpu_dirs), 1)

        util = self._util_percent(before["stat"], after["stat"])

        freqs = after["freq_khz"]
        busy_mhz = (sum(freqs) / len(freqs) / 1000.0) if freqs else None
        avg_mhz = (
            busy_mhz * (util / 100.0)
            if busy_mhz is not None and util is not None else None
        )

        sample = {
            "timestamp_ns": after["t_ns"],
            "sample_start_ns": before["t_ns"],
            "sample_end_ns": after["t_ns"],
            "interval_ns": interval_ns,
            "cpu_util_percent": util,
            "cpu_busy_mhz": busy_mhz,
            "cpu_avg_mhz": avg_mhz,
            "package_power": self._rapl_watts(before, after, interval_s),
            "package_temp": (
                after["temp_mc"] / 1000.0 if after["temp_mc"] is not None else None
            ),
        }

        # Idle residency: share of total cpu time the interval spent in
        # each state. Named C states get their column; everything else
        # (POLL, vendor states) is preserved in extra_metrics_json.
        extra = {"measurement_source": "cpuidle_sysfs"}
        denom_us = interval_s * 1e6 * n_cpus
        for state, total_us in after["idle"].items():
            prev_us = before["idle"].get(state)
            if total_us is None or prev_us is None or denom_us <= 0:
                continue
            share = max(total_us - prev_us, 0) / denom_us * RESIDENCY_SCALE
            key = state.upper()
            if key in _COLUMN_STATES:
                sample[key.lower() + "_residency"] = share
            else:
                extra[key.lower() + "_residency"] = share
        sample["extra_metrics_json"] = json.dumps(extra)
        return sample

    @staticmethod
    def _util_percent(stat_before, stat_after):
        """Busy share of /proc/stat aggregate jiffies, 0 to 100."""
        if not stat_before or not stat_after:
            return None
        d_total = stat_after["total"] - stat_before["total"]
        if d_total <= 0:
            return None
        d_idle = stat_after["idle_all"] - stat_before["idle_all"]
        return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))

    def _rapl_watts(self, before, after, interval_s):
        """RAPL package delta over the interval, wrap corrected."""
        if before["rapl_uj"] is None or after["rapl_uj"] is None:
            return None
        d_uj = after["rapl_uj"] - before["rapl_uj"]
        if d_uj < 0:
            # Counter wrapped: add the hardware range (0 disables fixup).
            d_uj += self._rapl["max_range"] if self._rapl else 0
        if d_uj < 0 or interval_s <= 0:
            return None
        return d_uj / 1e6 / interval_s

    # ------------------------------------------------------------------
    # Low level readers — never raise, log once per source (DC-3, PAC-4)
    # ------------------------------------------------------------------

    def _read_proc_stat(self):
        """
        Aggregate cpu line from /proc/stat.

        Returns:
            dict: total jiffies and idle_all (idle + iowait), or None.
        """
        try:
            with open("/proc/stat") as f:
                fields = f.readline().split()[1:]
            vals = [int(v) for v in fields]
            # Field order: user nice system idle iowait irq softirq steal
            idle_all = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return {"total": sum(vals[:8]), "idle_all": idle_all}
        except (IOError, OSError, ValueError, IndexError) as e:
            self._warn_once("/proc/stat", "read failed: %s" % e)
            return None

    def _sum_files(self, paths, tag):
        """Sum integer file contents across CPUs; None if all fail."""
        total, seen = 0, False
        for p in paths:
            v = self._read_int(p, tag)
            if v is not None:
                total += v
                seen = True
        return total if seen else None

    def _read_int(self, path, tag=None):
        """Read one integer file; None on any failure, warned once."""
        if path is None:
            return None
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (IOError, OSError, ValueError) as e:
            self._warn_once(tag or path, "read failed %s: %s" % (path, e))
            return None

    def _read_str(self, path):
        """Read one stripped string file; None on failure (discovery)."""
        try:
            with open(path) as f:
                return f.read().strip()
        except (IOError, OSError):
            return None

    def _warn_once(self, key, msg):
        """Log a source failure once per run, not once per tick."""
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning("SysfsCPUReader: %s", msg)
