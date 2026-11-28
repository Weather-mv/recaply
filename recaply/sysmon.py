"""
CPU, RAM and GPU load for the dashboard's system widget. Standard library only (no psutil):
Windows uses GetSystemTimes / GlobalMemoryStatusEx, Linux reads /proc, the GPU comes from `nvidia-smi`
when there is one. Every figure is a percentage or None when this PC can't tell.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

_lock = threading.Lock()
_prev_cpu: tuple[float, float] | None = None
_gpu_cache: tuple[float, dict | None] = (0.0, None)
GPU_TTL = 4.0


def _cpu_times() -> tuple[float, float] | None:
    """(busy, total) counters since boot."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            idle, kern, user = (wintypes.FILETIME() for _ in range(3))
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            val = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime  # noqa: E731
            i, k, u = val(idle), val(kern), val(user)
            total = k + u                           # kernel time already includes idle time
            return float(total - i), float(total)
        with open("/proc/stat", encoding="utf-8") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts) - idle, sum(parts)
    except Exception:
        return None


def cpu_percent() -> float | None:
    """Load since the previous call (the first call looks 0.25 s back)."""
    global _prev_cpu
    with _lock:
        prev = _prev_cpu or _cpu_times()
        if prev is None:
            return None
        if _prev_cpu is None:
            time.sleep(0.25)
        now = _cpu_times()
        if now is None:
            return None
        _prev_cpu = now
        dbusy, dtotal = now[0] - prev[0], now[1] - prev[1]
        return round(min(100.0, max(0.0, 100.0 * dbusy / dtotal)), 1) if dtotal > 0 else 0.0


def memory() -> dict | None:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            total, avail = m.ullTotalPhys, m.ullAvailPhys
        else:
            info = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    info[k] = float(v.split()[0]) * 1024
            total, avail = info["MemTotal"], info.get("MemAvailable", info["MemFree"])
        used = total - avail
        return {"percent": round(100.0 * used / total, 1), "used_gb": round(used / 2**30, 1), "total_gb": round(total / 2**30, 1)}
    except Exception:
        return None


def gpu() -> dict | None:
    """First NVIDIA GPU: load, memory and name (cached for a few seconds, nvidia-smi is slowish)."""
    global _gpu_cache
    at, val = _gpu_cache
    if time.time() - at < GPU_TTL:
        return val
    out = None
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            r = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
            name, util, used, total = [x.strip() for x in line.split(",")]
            out = {"name": name, "percent": float(util), "mem_percent": round(100.0 * float(used) / float(total), 1),
                   "used_gb": round(float(used) / 1024, 1), "total_gb": round(float(total) / 1024, 1)}
        except Exception:
            out = None
    _gpu_cache = (time.time(), out)
    return out


def snapshot() -> dict:
    return {"cpu": cpu_percent(), "ram": memory(), "gpu": gpu(), "ts": time.time()}
