"""
Fallback Disk Reader — unknown/unsupported platforms.

Used by factory when neither Linux /proc/diskstats nor macOS IOKit
is available. Returns None for all samples — never raises.

Platforms: ARM VM without /proc, Windows, unknown OS.
"""

from typing import Optional, Dict
from core.readers.interfaces import DiskReaderABC


class FallbackDiskReader(DiskReaderABC):
    """No-op disk reader for unsupported platforms."""

    # SPEC 35A: fallback disk is NOT registered — factory fallback only.
    METHOD_ID: str = "disk_reader_fallback"
    PRIORITY: int  = 999
 
    @classmethod
    def can_handle(cls, caps) -> bool:
        """Fallback never enters registry — factory uses it as LIMITED fallback."""
        return False
 
    def get_name(self) -> str:
        """Return reader name for logging."""
        return "FallbackDiskReader"
 
    def __init__(self, config: dict = None, device: str = "", pid: int = 0):
        self.device = device
        self.pid    = pid
        self._last  = None

    def is_available(self) -> bool:
        """Never available — this is the last-resort fallback."""
        return False

    def sample(self) -> Optional[Dict]:
        """Returns None — no disk I/O data available on this platform."""
        return None

    def _detect_device(self) -> str:
        """No device to detect."""
        return ""
