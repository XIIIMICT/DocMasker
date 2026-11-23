"""Shared benchmark helpers: schema conversion and peak RSS on Windows/Linux."""

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path

from src.masking.schemas import MaskedEntity


def detect_entities(masker, text: str) -> list[MaskedEntity]:
    """Run the detector and convert internal entities to the API schema."""
    return [
        MaskedEntity(label=e.label, text=e.text, start=e.start, end=e.end, score=e.score) for e in masker.detect(text)
    ]


class MemoryInfo(ctypes.Structure):
    """PROCESS_MEMORY_INFORMATION layout for GetProcessMemoryInfo (Windows only)."""

    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_peak_rss_mb() -> float:
    # `ctypes.windll` only exists on Windows. Access it via `getattr` so a
    # Linux type checker (CI `ty check`) does not report a missing attribute;
    # this function is only called when `sys.platform == "win32"` anyway.
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return 0.0
    psapi = windll.psapi
    kernel32 = windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(MemoryInfo), ctypes.wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL
    info = MemoryInfo()
    info.cb = ctypes.sizeof(MemoryInfo)
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
        return 0.0
    return info.PeakWorkingSetSize / (1024 * 1024)


def rss_mb() -> float:
    """Peak process RSS in MB (WorkingSet on Windows, VmHWM on Linux)."""
    if sys.platform == "win32":
        return _windows_peak_rss_mb()
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024.0
    return 0.0
