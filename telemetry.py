"""
telemetry.py — live system stats for the ULTRON dashboard.

Tuned for the Dell 15 / Ryzen 3 7320U with integrated Radeon graphics:
- GPU is integrated, so we use Windows GPU Engine counters (best effort)
- VRAM is shared from system RAM, so we show disk / battery instead
"""

import os
import threading
import time

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False

try:
    import win32pdh
    WIN32PDH_OK = True
except ImportError:
    WIN32PDH_OK = False


_lock = threading.Lock()


# ---------------------------------------------------------------
# GPU LOAD — via Windows PDH 'GPU Engine' counter (works for AMD/NVIDIA/Intel)
# ---------------------------------------------------------------
def _get_gpu_load():
    """Return int 0-100 for total GPU utilization, or None."""
    if not WIN32PDH_OK:
        return None

    try:
        query = win32pdh.OpenQuery()
        counters = []
        try:
            _, instances = win32pdh.EnumObjectItems(
                None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD
            )
            for inst in instances:
                try:
                    path = win32pdh.MakeCounterPath(
                        (None, "GPU Engine", inst, None, 0, "Utilization Percentage")
                    )
                    counters.append(win32pdh.AddCounter(query, path))
                except Exception:
                    continue

            if not counters:
                return None

            win32pdh.CollectQueryData(query)
            time.sleep(0.05)
            win32pdh.CollectQueryData(query)

            total = 0.0
            for c in counters:
                try:
                    _, val = win32pdh.GetFormattedCounterValue(c, win32pdh.PDH_FMT_DOUBLE)
                    if val > 0:
                        total += val
                except Exception:
                    continue
            return min(100, int(total))
        finally:
            try:
                win32pdh.CloseQuery(query)
            except Exception:
                pass
    except Exception:
        return None


# ---------------------------------------------------------------
# CPU TEMPERATURE — often blank on Ryzen laptops, best effort
# ---------------------------------------------------------------
def _get_cpu_temp():
    if not PSUTIL_OK:
        return None
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower"):
            if key in temps and temps[key]:
                return round(temps[key][0].current, 0)
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 0)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------
def get_stats():
    """Collect all stats. Safe to call from any thread."""
    with _lock:
        stats = {
            "cpu_percent": 0,
            "ram_percent": 0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "disk_percent": 0,
            "disk_free_gb": 0.0,
            "disk_total_gb": 0.0,
            "gpu_percent": None,
            "cpu_temp_c": None,
            "battery_pct": None,
            "battery_plugged": None,
        }

        if PSUTIL_OK:
            try:
                stats["cpu_percent"] = int(psutil.cpu_percent(interval=None))
            except Exception:
                pass

            try:
                vm = psutil.virtual_memory()
                stats["ram_percent"] = int(vm.percent)
                stats["ram_used_gb"] = round(vm.used / (1024 ** 3), 1)
                stats["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
            except Exception:
                pass

            try:
                du = psutil.disk_usage("C:\\")
                stats["disk_percent"] = int(du.percent)
                stats["disk_free_gb"] = round(du.free / (1024 ** 3), 1)
                stats["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
            except Exception:
                pass

            stats["cpu_temp_c"] = _get_cpu_temp()

            try:
                bat = psutil.sensors_battery()
                if bat:
                    stats["battery_pct"] = int(bat.percent)
                    stats["battery_plugged"] = bool(bat.power_plugged)
            except Exception:
                pass

        stats["gpu_percent"] = _get_gpu_load()

        return stats