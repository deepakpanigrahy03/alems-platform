"""
GPU energy collector for A-LEMS platform — Chunk 15-A.

Mirrors HighFrequencySampler pattern from core/utils/sampling.py exactly.
Runs in background thread at 10 Hz alongside existing energy_engine sampling.

Backend detection order (15-A implements MSR + None; 15-B adds the rest):
    DCGM     → GN100 / DGX systems (15-B)
    NVML     → NVIDIA discrete GPUs (15-B)
    MSR PP1  → Intel integrated GPU, UBUNTU2505 Iris Xe (THIS CHUNK)
    ROCm     → AMD GPUs (15-B stub)
    IOKit    → Apple Silicon (15-B stub)
    None     → graceful fallback, no samples collected

PAC-2 compliant: GPUCollector is the ONLY place that imports GPU backends.
Never import backends directly in energy_engine.py or harness.py.

core/readers/gpu_collector.py
"""

import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 10 Hz matches cpu_samples and interrupt_samples rate.
# NVML caps at ~50 Hz; 10 Hz balances detail vs collector overhead.
DEFAULT_SAMPLING_HZ = 10

# B9 fix: the sample queue is unbounded. A bounded queue with oldest drop lost
# the start of every run longer than about 200 s at 10 Hz. One hour at 10 Hz
# is about 36,000 small objects, so memory is not a concern.
# Readiness protocol and coverage record (method dcgm_shared_window_coverage_v1):
READY_TIMEOUT_S = 5.0     # longest wait for a first valid reading before the run window
READY_POLL_S = 0.05       # poll interval while waiting for readiness
INTERIOR_GAP_MS = 300.0   # gap between consecutive sample ends reported above this


def coverage_for_run(cov, start_ns, end_ns):
    # type: (dict, Optional[int], Optional[int]) -> tuple
    """
    Share of the run window [start_ns, end_ns] covered by valid GPU samples.

    Coverage is the overlap of [first_valid, last_valid] with the task window [t0, t1]
    (the run window for legacy runs without marks),
    divided by the window length. Interior gaps are reported separately.

    Returns:
        (coverage in [0, 1], None) or (None, reason). Never 0 for unknown (MIC-1).
    """
    if cov.get("ready_status") != "ready":
        return None, "readiness_" + str(cov.get("ready_status"))
    if start_ns is None or end_ns is None or end_ns <= start_ns:
        return None, "run_window_unknown"
    first, last = cov.get("first_valid_utc_ns"), cov.get("last_valid_utc_ns")
    if first is None or last is None:
        return None, "no_valid_samples"
    overlap = min(last, end_ns) - max(first, start_ns)
    return max(0.0, min(1.0, overlap / float(end_ns - start_ns))), None


@dataclass
class GpuSample:
    """
    One GPU energy sample. Mirrors EnergySample shape from sampling.py.
    source column identifies backend — critical for paper methodology section.
    Reviewer will ask: how did you measure GPU energy on each platform?
    """
    gpu_index: int
    sample_start_ns: int
    sample_end_ns: int
    interval_ns: int
    energy_start_uj: Optional[int]
    energy_end_uj: Optional[int]
    energy_uj: Optional[int]       # denorm delta for query speed
    power_mw: Optional[int]
    util_gpu_pct: Optional[float]
    util_mem_pct: Optional[float]
    sm_clock_mhz: Optional[int]
    mem_clock_mhz: Optional[int]
    mem_used_mb: Optional[int]
    temperature_c: Optional[int]
    source: str

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Convert to dict for DB INSERT via samples repository."""
        return {
            'gpu_index':       self.gpu_index,
            'sample_start_ns': self.sample_start_ns,
            'sample_end_ns':   self.sample_end_ns,
            'interval_ns':     self.interval_ns,
            'energy_start_uj': self.energy_start_uj,
            'energy_end_uj':   self.energy_end_uj,
            'energy_uj':       self.energy_uj,
            'power_mw':        self.power_mw,
            'util_gpu_pct':    self.util_gpu_pct,
            'util_mem_pct':    self.util_mem_pct,
            'sm_clock_mhz':    self.sm_clock_mhz,
            'mem_clock_mhz':   self.mem_clock_mhz,
            'mem_used_mb':     self.mem_used_mb,
            'temperature_c':   self.temperature_c,
            'source':          self.source,
        }


class NoneBackend:
    """
    Fallback backend. Returns None for all reads.
    Used when no GPU energy backend is available on this platform.
    PAC-4 compliant: never raises, always returns None gracefully.
    """
    SOURCE = 'none'

    def is_available(self):
        # type: () -> bool
        return False

    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        """No backend available — always None."""
        return None

    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        """No signals available."""
        return {}

    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        """No GPU info available."""
        return []


class MSRPP1Backend:
    """
    Intel integrated GPU backend via MSR 0x641 (MSR_PP1_ENERGY_STATUS).
    Verified on UBUNTU2505: Intel i7-1165G7 + Iris Xe Graphics.

    Energy unit: 61.0352 µJ/LSB (MSR 0x606 bits[12:8]=14, verified).
    No sudo required on UBUNTU2505 (msr module loaded, group permissions set).

    GPU is in PKG remainder bucket: PKG = core + uncore + remainder.
    GPU is sub-component of remainder, NOT inside uncore.
    intel-rapl:0:1 sysfs = uncore domain (L3/ring bus), NOT GPU.

    No util/clock/memory signals via this backend — those columns NULL.
    """
    SOURCE = 'msr_pp1'
    # 2^(-14) J = 61.0352 µJ per LSB — from MSR 0x606 bits[12:8]=14
    ENERGY_UNIT_UJ = 61.0352

    def __init__(self, rapl_reader):
        """
        Args:
            rapl_reader: RAPLReader instance with gpu_pp1_available flag
                         and read_gpu_msr() method. Injected for PAC-2 compliance.
        """
        self._rapl = rapl_reader
        # Cache model/driver from rapl_reader if available
        self._gpu_model = getattr(rapl_reader, '_gpu_model', 'Intel Iris Xe')
        self._gpu_driver = getattr(rapl_reader, '_gpu_driver', 'i915')

    def is_available(self):
        # type: () -> bool
        """Returns True only if MSR 0x641 was successfully probed at init."""
        return bool(getattr(self._rapl, 'gpu_pp1_available', False))

    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        """
        Read current MSR 0x641 counter and convert to µJ.
        gpu_index ignored: only one integrated GPU on supported platforms.
        Returns None on read failure — never raises (PAC-4 compliant).
        """
        raw = self._rapl.read_gpu_msr()
        if raw is None:
            return None
        # Convert raw LSB count to µJ using verified energy unit
        return int(raw * self.ENERGY_UNIT_UJ)

    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        """MSR PP1 exposes energy only — no util/clock/memory signals."""
        return {}

    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        """
        Returns metadata dict for gpu_config INSERT.
        Model and driver sourced from hw_config.json gpu block via rapl_reader.
        """
        return [{
            'gpu_index':        0,
            'vendor':           'intel',
            'model':            self._gpu_model,
            'driver_version':   self._gpu_driver,
            'cuda_version':     None,
            'rocm_version':     None,
            'vbios_version':    None,
            'pci_id':           None,
            'memory_total_mb':  None,
            'energy_supported': 1,
            'backend':          self.SOURCE,
        }]

class NVMLBackend:
    """
    NVIDIA GPU backend via nvidia-ml-py (pynvml).
    Covers: discrete NVIDIA GPUs (RTX 2070 Super verified), NVIDIA GB10 fallback if DCGM absent.
 
    Primary: nvmlDeviceGetTotalEnergyConsumption() — cumulative mJ counter.
    Fallback: nvmlDeviceGetPowerUsage() instantaneous mW when counter absent.
    Confidence: 1.0 cumulative, 0.85 power-integration fallback.
    """
    SOURCE = 'nvml'
 
    def __init__(self):
        self._handle = None
        self._gpu_count = 0
        self._has_energy_counter = False
        self._pynvml = None
        self._init_nvml()
 
    def _init_nvml(self):
        # type: () -> None
        """Initialize NVML. Sets _handle=None on failure — never raises."""
        try:
            import pynvml
            pynvml.nvmlInit()
            self._pynvml = pynvml
            self._gpu_count = pynvml.nvmlDeviceGetCount()
            if self._gpu_count == 0:
                return
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            # Probe cumulative energy counter — not available on all drivers
            try:
                pynvml.nvmlDeviceGetTotalEnergyConsumption(self._handle)
                self._has_energy_counter = True
                logger.info("NVMLBackend: cumulative energy counter available")
            except pynvml.NVMLError:
                self._has_energy_counter = False
                logger.info("NVMLBackend: falling back to power integration")
        except Exception as e:
            logger.debug("NVMLBackend init failed: %s", e)
            self._handle = None
 
    def is_available(self):
        # type: () -> bool
        return self._handle is not None
 
    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        """
        Read cumulative energy in µJ (mJ counter x1000) or mW for integration.
        Returns None on failure — never raises (PAC-4 compliant).
        """
        if self._handle is None:
            return None
        try:
            if self._has_energy_counter:
                # nvmlDeviceGetTotalEnergyConsumption returns mJ — convert to µJ
                mj = self._pynvml.nvmlDeviceGetTotalEnergyConsumption(self._handle)
                return int(mj * 1000)
            else:
                # Instantaneous mW — GPUCollector integrates power x dt
                mw = self._pynvml.nvmlDeviceGetPowerUsage(self._handle)
                return int(mw)
        except Exception as e:
            logger.warning("NVMLBackend.read_energy_uj failed: %s", e)
            return None
 
    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        """Read GPU utilization, clocks, memory, temperature via NVML."""
        if self._handle is None:
            return {}
        signals = {}
        try:
            util = self._pynvml.nvmlDeviceGetUtilizationRates(self._handle)
            signals['util_gpu_pct'] = float(util.gpu)
            signals['util_mem_pct'] = float(util.memory)
        except Exception:
            pass
        try:
            signals['sm_clock_mhz'] = int(
                self._pynvml.nvmlDeviceGetClockInfo(
                    self._handle, self._pynvml.NVML_CLOCK_SM))
            signals['mem_clock_mhz'] = int(
                self._pynvml.nvmlDeviceGetClockInfo(
                    self._handle, self._pynvml.NVML_CLOCK_MEM))
        except Exception:
            pass
        try:
            mem = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            signals['mem_used_mb'] = int(mem.used / (1024 * 1024))
        except Exception:
            pass
        try:
            signals['temperature_c'] = int(
                self._pynvml.nvmlDeviceGetTemperature(
                    self._handle, self._pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            pass
        return signals
 
    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        """Returns metadata list for gpu_config INSERT."""
        if self._handle is None:
            return []
        info_list = []
        for i in range(self._gpu_count):
            try:
                h = self._pynvml.nvmlDeviceGetHandleByIndex(i)
                name = self._pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode()
                mem = self._pynvml.nvmlDeviceGetMemoryInfo(h)
                driver = self._pynvml.nvmlSystemGetDriverVersion()
                if isinstance(driver, bytes):
                    driver = driver.decode()
                cuda_ver = None
                try:
                    cv = self._pynvml.nvmlSystemGetCudaDriverVersion()
                    cuda_ver = "{}.{}".format(cv // 1000, (cv % 1000) // 10)
                except Exception:
                    pass
                info_list.append({
                    'gpu_index':        i,
                    'vendor':           'nvidia',
                    'model':            name,
                    'driver_version':   driver,
                    'cuda_version':     cuda_ver,
                    'rocm_version':     None,
                    'vbios_version':    None,
                    'pci_id':           None,
                    'memory_total_mb':  int(mem.total / (1024 * 1024)),
                    'energy_supported': 1 if self._has_energy_counter else 0,
                    'backend':          self.SOURCE,
                })
            except Exception as e:
                logger.warning("NVMLBackend.get_gpu_info gpu %d failed: %s", i, e)
        return info_list
 
    def shutdown(self):
        """Clean NVML shutdown."""
        try:
            self._pynvml.nvmlShutdown()
        except Exception:
            pass
 
 
# DCGM Python bindings locations, newest first. DCGM 4 moved them; a fixed
# path broke GPU energy silently after the upgrade (B24).
# ALEMS_DCGM_BINDINGS overrides for nonstandard installs.
_DCGM_BINDING_PATHS = (
    "/usr/share/datacenter-gpu-manager-4/bindings/python3",  # DCGM 4
    "/usr/local/dcgm/bindings/python3",                       # DCGM 3
)


def _dcgm_binding_candidates():
    # type: () -> List[str]
    """Candidate folders for pydcgm, override first."""
    override = os.environ.get("ALEMS_DCGM_BINDINGS")
    return ([override] if override else []) + list(_DCGM_BINDING_PATHS)


def _ensure_dcgm_bindings():
    # type: () -> Optional[str]
    """
    Make pydcgm importable without relying on a hand written .pth file.

    Returns:
        The folder added to sys.path, "already" if pydcgm was importable, or
        None if no candidate holds pydcgm.py.
    """
    try:
        import pydcgm  # noqa: F401  (already importable via .pth or PYTHONPATH)
        return "already"
    except ImportError:
        pass
    for path in _dcgm_binding_candidates():
        if not os.path.isfile(os.path.join(path, "pydcgm.py")):
            continue
        if path not in sys.path:
            sys.path.insert(0, path)
        return path
    return None


def _dcgm_installed():
    # type: () -> bool
    """True if the DCGM daemon binary exists: then a load failure is an error, not a skip."""
    import shutil
    return shutil.which("nv-hostengine") is not None


class DCGMBackend:
    """
    NVIDIA DCGM backend for DGX/HGX systems.
    Primary platform: GN100 (NVIDIA GB10 Superchip, ARM aarch64).
 
    DCGM field 156 = DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION (cumulative mJ).
    Validated on GN100: spark_hwmon loaded, 4 energy accumulators confirmed.
    RAPL absent on GB10. Package and rail energy come from SPBM; DCGM field 156
    is the GPU counter used for GPU attribution and the SPBM to DCGM cross check.
 
    Bindings located by _ensure_dcgm_bindings() (DCGM 4 path, then DCGM 3,
    or ALEMS_DCGM_BINDINGS); scripts/setup_dcgm_venv.sh also registers them via .pth.
    Run scripts/setup_dcgm_venv.sh once after activating venv on GN100.
    """
    SOURCE = 'dcgm'
    # DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION — cumulative mJ counter
    FIELD_TOTAL_ENERGY = 156
    # DCGM_FI_DEV_GPU_UTIL — GPU utilization percent
    FIELD_GPU_UTIL = 203
    # DCGM_FI_DEV_GPU_TEMP — GPU temperature Celsius
    FIELD_GPU_TEMP = 150
 
    def __init__(self):
        self._handle = None
        self._system = None
        self._init_dcgm()
 
    def _init_dcgm(self):
        # type: () -> None
        """
        Connect to local DCGM daemon and set up field watch on energy field 156.
        Uses DcgmGroup + WatchFields pattern — validated on GN100 GB10.
        Sets _handle=None on any failure so is_available() returns False cleanly.
        """
        # B24: locate the bindings before importing (DCGM 4 moved them)
        self._bindings_path = _ensure_dcgm_bindings()
        try:
            import pydcgm
            import dcgm_fields
            self._pydcgm = pydcgm
            self._dcgm_fields = dcgm_fields
            # Connect to daemon — must be running (nvidia-dcgm.service)
            self._handle = pydcgm.DcgmHandle(ipAddress='127.0.0.1')
            self._system = self._handle.GetSystem()
            # Create group containing GPU 0 for field watching
            # B26: unique names per backend instance; fixed names collided ("Duplicate key")
            # when two backends lived in one process or a crashed run left its group behind.
            _tag = "%d_%d" % (os.getpid(), id(self) & 0xFFFF)
            self._group = pydcgm.DcgmGroup(self._handle, groupName='alems_gpu_energy_' + _tag)
            self._group.AddGpu(0)
            # Create field group for energy + util + temp fields
            self._field_group = pydcgm.DcgmFieldGroup(
                self._handle, 'alems_fields_' + _tag,
                [self.FIELD_TOTAL_ENERGY, self.FIELD_GPU_UTIL, self.FIELD_GPU_TEMP])
            # Watch at 100ms interval — GPUCollector samples at 10Hz so this is fine
            self._group.samples.WatchFields(self._field_group, 100000, 1.0, 0)
            logger.info("DCGMBackend: connected to DCGM daemon, watching field 156")
            # G191: register cleanup when the connection exists, not when sampling
            # starts, so an exit before any measurement (preflight failure) also
            # releases the field group before the handle. cleanup() is idempotent,
            # so the later registration in GPUCollector.start() is harmless.
            import atexit
            atexit.register(self.cleanup)
        except Exception as e:
            self._handle = None
            if not _dcgm_installed():
                # No DCGM on this machine (x86, Mac): expected, stay quiet
                logger.debug("DCGMBackend init skipped (DCGM not installed): %s", e)
                return
            # DCGM is installed, so missing GPU energy is a measurement failure (FAD-2, DC-3)
            logger.error(
                "DCGMBackend: DCGM installed but unusable: %s. bindings=%s; searched %s. "
                "GPU energy (DCGM field 156) will be missing from this run.",
                e, self._bindings_path, _dcgm_binding_candidates())
 
    def cleanup(self):
        # type: () -> None
        """
        Explicitly release DCGM resources before process exit.
        Prevents DcgmFieldGroup.__del__ firing during Python shutdown
        when the handle is already gone — eliminates DCGMError_Badparam
        on stderr. Safe to call multiple times.
        """
        if self._handle is None:
            return
        try:
            self._field_group.Delete()
            # B26: delete the group too, so the daemon keeps no stale alems groups
            # (DCGM caps the number of groups; leaked groups eventually block new runs)
            grp = getattr(self, "_group", None)
            if grp is not None:
                grp.Delete()
        except Exception:
            pass
        try:
            self._handle.Disconnect()
        except Exception:
            pass
        self._handle = None
        logger.debug("DCGMBackend: cleanup complete")

    def is_available(self):
        # type: () -> bool
        return self._handle is not None
 
    def _get_latest(self):
        # type: () -> Optional[object]
        """Refresh DCGM fields and return latest value collection."""
        if self._handle is None:
            return None
        try:
            self._system.UpdateAllFields(1)
            return self._group.samples.GetLatest(self._field_group)
        except Exception as e:
            logger.warning("DCGMBackend._get_latest failed: %s", e)
            return None
 
    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        """
        Read DCGM field 156 cumulative energy.
        DCGM returns raw counter — unit confirmed as mJ on GN100 (fv.value=34771379 at idle).
        Converted to µJ by x1000 for platform consistency.
        Returns None on failure — never raises (PAC-4 compliant).
        """
        collection = self._get_latest()
        if collection is None:
            return None
        try:
            # Navigate: collection.values[gpu_id][field_id].values[0].value
            fv = collection.values[gpu_index][self.FIELD_TOTAL_ENERGY].values[0]
            if fv.isBlank:
                return None
            # Multiply by 1000 to convert mJ to µJ
            return int(fv.value * 1000)
        except Exception as e:
            logger.warning("DCGMBackend.read_energy_uj failed: %s", e)
            return None
 
    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        """Read GPU utilization and temperature via DCGM fields 203 and 150."""
        collection = self._get_latest()
        if collection is None:
            return {}
        signals = {}
        try:
            fv_util = collection.values[gpu_index][self.FIELD_GPU_UTIL].values[0]
            if not fv_util.isBlank:
                signals['util_gpu_pct'] = float(fv_util.value)
        except Exception:
            pass
        try:
            fv_temp = collection.values[gpu_index][self.FIELD_GPU_TEMP].values[0]
            if not fv_temp.isBlank:
                signals['temperature_c'] = int(fv_temp.value)
        except Exception:
            pass
        return signals
 
    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        """Returns metadata list for gpu_config INSERT."""
        if self._system is None:
            return []
        try:
            gpus = self._system.discovery.GetAllGpuIds()
            info_list = []
            for i, gpu_id in enumerate(gpus):
                attrs = self._system.discovery.GetGpuAttributes(gpu_id)
                info_list.append({
                    'gpu_index':        i,
                    'vendor':           'nvidia',
                    'model':            attrs.identifiers.deviceName,
                    'driver_version':   attrs.identifiers.driverVersion,
                    'cuda_version':     None,
                    'rocm_version':     None,
                    'vbios_version':    attrs.identifiers.vbios,
                    'pci_id':           None,
                    'memory_total_mb':  None,
                    'energy_supported': 1,
                    'backend':          self.SOURCE,
                })
            return info_list
        except Exception as e:
            logger.warning("DCGMBackend.get_gpu_info failed: %s", e)
            return []
 
 
class IOKitBackend:
    """
    Apple Silicon GPU energy backend via powermetrics.
    Platform: macOS only, Apple Silicon (M1 Pro verified).
 
    powermetrics exposes instantaneous GPU power in mW per sample.
    GPUCollector integrates power x dt to derive energy in µJ.
    Requires sudo on macOS — warns if unavailable.
    Confidence: 0.90 (Apple internal counter, not independently validated).
    """
    SOURCE = 'iokit'
 
    def __init__(self):
        # Probe once at init — avoids repeated sudo calls during sampling
        self._available = self._probe()
 
    def _probe(self):
        # type: () -> bool
        """Return True only on macOS with accessible powermetrics."""
        import platform as _platform
        if _platform.system() != 'Darwin':
            return False
        try:
            import subprocess
            result = subprocess.run(
                ['sudo', 'powermetrics', '--samplers', 'gpu_power',
                 '-n', '1', '-i', '100'],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
 
    def is_available(self):
        # type: () -> bool
        return self._available
 
    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        """
        Returns instantaneous GPU power in mW via powermetrics.
        GPUCollector multiplies by dt to get energy — stored in power_mw field.
        Returns None on failure.
        """
        if not self._available:
            return None
        try:
            import subprocess
            import re
            result = subprocess.run(
                ['sudo', 'powermetrics', '--samplers', 'gpu_power',
                 '-n', '1', '-i', '100', '--format', 'plist'],
                capture_output=True, timeout=5, text=True
            )
            match = re.search(r'gpu_power.*?(\d+\.?\d*)', result.stdout)
            if match:
                return int(float(match.group(1)))
            return None
        except Exception as e:
            logger.warning("IOKitBackend.read_energy_uj failed: %s", e)
            return None
 
    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        """No additional signals from powermetrics in current implementation."""
        return {}
 
    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        """Apple GPU info from system_profiler."""
        try:
            import subprocess
            import json as _json
            result = subprocess.run(
                ['system_profiler', 'SPDisplaysDataType', '-json'],
                capture_output=True, text=True, timeout=10
            )
            data = _json.loads(result.stdout)
            gpus = data.get('SPDisplaysDataType', [{}])
            return [{
                'gpu_index':        0,
                'vendor':           'apple',
                'model':            gpus[0].get('sppci_model', 'Apple GPU'),
                'driver_version':   None,
                'cuda_version':     None,
                'rocm_version':     None,
                'vbios_version':    None,
                'pci_id':           None,
                'memory_total_mb':  None,
                'energy_supported': 1,
                'backend':          self.SOURCE,
            }]
        except Exception:
            return []
 
 
class ROCmBackend:
    """
    AMD GPU backend via ROCm SMI.
    STUB ONLY — no AMD GPU hardware in lab as of 2026-06.
    Interface matches all other backends exactly for future activation.
    Activate when AMD hardware joins the lab.
    """
    SOURCE = 'rocm_smi'
 
    def is_available(self):
        # type: () -> bool
        """Stub — always False until AMD hardware joins the lab."""
        return False
 
    def read_energy_uj(self, gpu_index=0):
        # type: (int) -> Optional[int]
        return None
 
    def read_signals(self, gpu_index=0):
        # type: (int) -> Dict[str, Any]
        return {}
 
    def get_gpu_info(self):
        # type: () -> List[Dict[str, Any]]
        return []

class GPUCollector:
    """
    GPU energy sampler. Runs in background thread at 10 Hz.
    Mirrors HighFrequencySampler from core/utils/sampling.py exactly.

    Lifecycle mirrors energy_engine._start_sampling / _stop_sampling:
        collector = GPUCollector(rapl_reader=rapl)
        collector.start()                    # before energy_engine.start_measurement()
        # ... workload runs ...
        samples = collector.stop()           # after energy_engine.stop_measurement()
        # persist via db.insert_gpu_samples(run_id, samples)

    Backend detection is automatic. NoneBackend used when no GPU energy
    source available — stop() returns empty list, no DB rows written.
    15-B adds NVML/DCGM/IOKit/ROCm backends to _detect_backend().
    """

    def __init__(self, rapl_reader=None, sampling_hz=None):
        """
        Args:
            rapl_reader: RAPLReader instance. Used for MSRPP1Backend detection.
                         Pass None on platforms without RAPL (e.g. GN100).
            sampling_hz: Sample rate. Default 10 Hz.
        """
        if sampling_hz is None:
            # B13: one policy for all samplers; DEFAULT_SAMPLING_HZ stays the hardware bound
            from core.readers.sampling_policy import effective_hz
            sampling_hz = effective_hz("GPU_COLLECTOR", DEFAULT_SAMPLING_HZ)
        self.sampling_hz = sampling_hz
        self.interval = 1.0 / sampling_hz

        # Detect backend — 15-B will expand this
        self.backend = self._detect_backend(rapl_reader)
        self.source = self.backend.SOURCE

        self._queue = queue.Queue()   # unbounded (B9): no sample is ever dropped
        self.last_coverage = {}       # coverage facts of the last stopped run
        self._ready_status = "unavailable"
        self._thread = None
        self._running = False
        self._samples_taken = 0
        self._samples_dropped = 0

        # Previous sample state for delta computation
        self._prev_energy_uj = None
        self._prev_ts_ns = None

        logger.info("GPUCollector: backend=%s available=%s",
                    self.source, self.backend.is_available())

    def _detect_backend(self, rapl_reader):
        # type: (Any) -> Any
        """
        Backend detection. First available wins.
        DCGM before NVML: on GN100, DCGM is the validated energy path.
        MSR PP1 before ROCm: Intel integrated is x86 only.
        NoneBackend: graceful fallback, never raises.
        """
        # DCGM — GN100/DGX systems, RAPL absent on ARM so DCGM is primary
        try:
            b = DCGMBackend()
            if b.is_available():
                logger.info("GPUCollector: using DCGMBackend")
                return b
        except Exception as e:
            logger.debug("DCGMBackend probe failed: %s", e)
 
        # NVML — NVIDIA discrete GPUs (RTX 2070, etc.)
        try:
            b = NVMLBackend()
            if b.is_available():
                logger.info("GPUCollector: using NVMLBackend")
                return b
        except Exception as e:
            logger.debug("NVMLBackend probe failed: %s", e)
 
        # MSR PP1 — Intel integrated GPU (UBUNTU2505 Iris Xe)
        if rapl_reader is not None:
            try:
                b = MSRPP1Backend(rapl_reader)
                if b.is_available():
                    logger.info("GPUCollector: using MSRPP1Backend")
                    return b
            except Exception as e:
                logger.debug("MSRPP1Backend probe failed: %s", e)
 
        # ROCm — AMD GPU stub, activates when hardware joins lab
        try:
            b = ROCmBackend()
            if b.is_available():
                logger.info("GPUCollector: using ROCmBackend")
                return b
        except Exception as e:
            logger.debug("ROCmBackend probe failed: %s", e)
 
        # IOKit — Apple Silicon (M1 Pro verified)
        try:
            b = IOKitBackend()
            if b.is_available():
                logger.info("GPUCollector: using IOKitBackend")
                return b
        except Exception as e:
            logger.debug("IOKitBackend probe failed: %s", e)
 
        logger.debug("GPUCollector: no backend available, using NoneBackend")
        return NoneBackend()

    def start(self):
        """
        Start background sampling thread.
        No-op if backend unavailable — stop() returns empty list.
        """
        if not self.backend.is_available():
            logger.debug("GPUCollector.start: no backend, skipping thread")
            return

        # Backend cleanup once per process at exit (G37), never per run.
        if hasattr(self.backend, "cleanup") and not getattr(self, "_cleanup_registered", False):
            import atexit
            atexit.register(self.backend.cleanup)
            self._cleanup_registered = True

        self._running = True
        self._samples_taken = 0
        self._samples_dropped = 0
        self._prev_energy_uj = None
        self._prev_ts_ns = None
        # B9: block until a valid reading, so the run window opens on a live
        # counter and the first delta is kept (it used to be skipped).
        self._wait_ready()

        self._thread = threading.Thread(
            target=self._sampling_loop,
            daemon=True,
            name="gpu-sampler",
        )
        self._thread.start()
        logger.debug("GPUCollector started at %d Hz", self.sampling_hz)

    def stop(self):
        # type: () -> List[GpuSample]
        """
        Stop sampling thread and return all collected samples.
        Safe to call even if start() was skipped (returns empty list).
        """
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # B9: keep the energy since the last tick (same rule as EnergyCollector)
        self._final_sample()

        samples = []
        while not self._queue.empty():
            try:
                samples.append(self._queue.get_nowait())
            except queue.Empty:
                break

        logger.info("GPUCollector stopped: %d samples taken, %d dropped",
                    self._samples_taken, self._samples_dropped)
        # B9: per run coverage facts, persisted to energy_validation_dcgm_coverage
        self.last_coverage = self._coverage_stats(samples)

        # No per run cleanup (G37): it closed the DCGM handle and every later
        # run in the process recorded no GPU samples. Cleanup runs at exit
        # (registered in start), which still prevents the BadParam traceback.

        return samples

    def get_sample_count(self):
        # type: () -> int
        """Current sample count — for coverage checks during run."""
        return self._samples_taken

    def _read_once(self):
        # type: () -> tuple
        """One energy read with its wall clock time; (ts_ns, None) on failure (PAC-4)."""
        ts_ns = time.time_ns()
        try:
            return ts_ns, self.backend.read_energy_uj(gpu_index=0)
        except Exception as e:
            logger.debug("GPUCollector readiness read failed: %s", e)
            return ts_ns, None

    def _wait_ready(self):
        # type: () -> None
        """
        Readiness protocol: poll until the backend returns a valid energy
        reading or READY_TIMEOUT_S passes. The valid reading becomes the
        baseline of the first delta. On timeout sampling still starts and
        the run's coverage is stored as NULL with a reason. Never raises.
        """
        self._sampler_start_ns = time.time_ns()
        self._ready_status = "timeout"
        self._first_valid_mono_ns = None
        self._last_valid_mono_ns = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < READY_TIMEOUT_S:
            ts_ns, value = self._read_once()
            if value is not None:
                self._ready_status = "ready"
                self._first_valid_mono_ns = time.monotonic_ns()
                self._last_valid_mono_ns = self._first_valid_mono_ns
                self._prev_energy_uj, self._prev_ts_ns = value, ts_ns
                break
            time.sleep(READY_POLL_S)
        self._ready_wait_ms = (time.monotonic() - t0) * 1000.0
        if self._ready_status != "ready":
            logger.warning("GPUCollector: no valid %s reading within %.1f s; "
                           "coverage will be NULL", self.source, READY_TIMEOUT_S)

    def _final_sample(self):
        # type: () -> None
        """
        One last reading after the sampling thread has stopped, so the
        interval between the last tick and stop() is kept. Skipped when no
        valid baseline exists (never a fabricated sample); a counter that
        went backwards is skipped as in the loop. Never raises (PAC-4).
        """
        if self._prev_energy_uj is None or self._prev_ts_ns is None:
            return
        ts_ns, energy_uj = self._read_once()
        if energy_uj is None or energy_uj < self._prev_energy_uj:
            return
        try:
            signals = self.backend.read_signals(gpu_index=0) or {}
        except Exception as e:
            logger.debug("GPUCollector final signals read failed: %s", e)
            signals = {}
        self._queue.put_nowait(GpuSample(
            gpu_index=0, sample_start_ns=self._prev_ts_ns, sample_end_ns=ts_ns,
            interval_ns=ts_ns - self._prev_ts_ns,
            energy_start_uj=self._prev_energy_uj, energy_end_uj=energy_uj,
            energy_uj=energy_uj - self._prev_energy_uj,
            power_mw=signals.get('power_mw'),
            util_gpu_pct=signals.get('util_gpu_pct'),
            util_mem_pct=signals.get('util_mem_pct'),
            sm_clock_mhz=signals.get('sm_clock_mhz'),
            mem_clock_mhz=signals.get('mem_clock_mhz'),
            mem_used_mb=signals.get('mem_used_mb'),
            temperature_c=signals.get('temperature_c'),
            source=self.source))
        self._samples_taken += 1
        self._last_valid_mono_ns = time.monotonic_ns()
        self._prev_energy_uj, self._prev_ts_ns = energy_uj, ts_ns

    def _coverage_stats(self, samples):
        # type: (list) -> Dict[str, Any]
        """
        Coverage facts of one run from its samples. Gaps are measured between
        consecutive sample end times, so a long interval and a skipped reading
        (blank value, wraparound) both count. cap_detected means a sample was
        dropped, which the unbounded queue only allows through a regression.
        """
        ends = sorted(s.sample_end_ns for s in samples)
        gaps_ns = [b - a for a, b in zip(ends, ends[1:])]
        limit_ns = int(INTERIOR_GAP_MS * 1e6)
        return {
            "source": self.source,
            "ready_status": getattr(self, "_ready_status", "unavailable"),
            "ready_wait_ms": getattr(self, "_ready_wait_ms", None),
            "sampler_start_utc_ns": getattr(self, "_sampler_start_ns", None),
            "first_valid_utc_ns": min((s.sample_start_ns for s in samples), default=None),
            "last_valid_utc_ns": ends[-1] if ends else None,
            "first_valid_mono_ns": getattr(self, "_first_valid_mono_ns", None),
            "last_valid_mono_ns": getattr(self, "_last_valid_mono_ns", None),
            "row_count": len(samples),
            "interior_gaps_gt_300ms": sum(1 for g in gaps_ns if g > limit_ns),
            "max_gap_ms": (max(gaps_ns) / 1e6) if gaps_ns else None,
            "cap_detected": self._samples_dropped > 0,
        }

    def _sampling_loop(self):
        """
        Background sampling loop at configured Hz.
        Delta computed between consecutive samples — mirrors energy_samples
        pkg_start_uj / pkg_end_uj pattern exactly.
        MSR counter wraparound (32-bit) handled with warning + skip.
        Queue full → oldest dropped (same policy as HighFrequencySampler).
        """
        logger.debug("GPUCollector._sampling_loop started")
        next_sample = time.time()

        while self._running:
            try:
                ts_ns = time.time_ns()
                energy_uj = self.backend.read_energy_uj(gpu_index=0)
                signals = self.backend.read_signals(gpu_index=0)

                # Skip first sample — no previous reading for delta
                if (self._prev_ts_ns is not None
                        and energy_uj is not None
                        and self._prev_energy_uj is not None):

                    interval_ns = ts_ns - self._prev_ts_ns
                    delta_uj = energy_uj - self._prev_energy_uj

                    if delta_uj < 0:
                        # Counter wrapped — skip sample, update prev, continue
                        logger.warning(
                            "GPUCollector: counter wraparound detected "
                            "(prev=%d cur=%d), skipping sample",
                            self._prev_energy_uj, energy_uj)
                    else:
                        sample = GpuSample(
                            gpu_index=0,
                            sample_start_ns=self._prev_ts_ns,
                            sample_end_ns=ts_ns,
                            interval_ns=interval_ns,
                            energy_start_uj=self._prev_energy_uj,
                            energy_end_uj=energy_uj,
                            energy_uj=delta_uj,
                            power_mw=signals.get('power_mw'),
                            util_gpu_pct=signals.get('util_gpu_pct'),
                            util_mem_pct=signals.get('util_mem_pct'),
                            sm_clock_mhz=signals.get('sm_clock_mhz'),
                            mem_clock_mhz=signals.get('mem_clock_mhz'),
                            mem_used_mb=signals.get('mem_used_mb'),
                            temperature_c=signals.get('temperature_c'),
                            source=self.source,
                        )
                        # Oldest-drop policy — same as HighFrequencySampler
                        try:
                            self._queue.put_nowait(sample)
                            self._samples_taken += 1
                        except queue.Full:
                            try:
                                self._queue.get_nowait()
                                self._queue.put_nowait(sample)
                                self._samples_dropped += 1
                            except queue.Empty:
                                pass

                if energy_uj is not None:
                    # last valid reading on the monotonic clock (coverage record)
                    self._last_valid_mono_ns = time.monotonic_ns()
                self._prev_energy_uj = energy_uj
                self._prev_ts_ns = ts_ns

            except Exception as e:
                # Never crash the sampling thread — log and continue
                logger.warning("GPUCollector sample error: %s", e)

            # Precise sleep to maintain configured Hz
            next_sample += self.interval
            sleep_time = next_sample - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.debug("GPUCollector._sampling_loop exited")
