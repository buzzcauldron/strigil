"""Light hardware autodetection for effective scraping (workers, concurrency)."""

import os
import re
import subprocess
import sys

# Cap workers for crawl; scale with CPU. Higher = faster crawl, more load.
MAX_WORKERS = 12
MIN_WORKERS = 1

# Per-page parallelism: asset downloads and image HEADs (for size filter).
SAFE_ASSET_WORKERS = 8
SAFE_HEAD_WORKERS = 6

# Aggressiveness presets: (crawl_workers, delay_seconds). "balanced" uses hw for workers.
AGGRESSIVENESS_PRESETS = {
    "conservative": {"workers": 2, "delay": 1.0},
    "balanced": {"workers": None, "delay": 0.4},  # workers from hw
    "aggressive": {"workers": 12, "delay": 0.15},
}
AGGRESSIVENESS_CHOICES = ("conservative", "balanced", "aggressive", "auto")


# Battery thresholds for throttling when on battery (0-100).
BATTERY_LOW_PERCENT = 20   # Below this: always conservative
BATTERY_OK_PERCENT = 50    # At or above: may use balanced if hardware allows


def _power_status() -> tuple[bool | None, int | None]:
    """Return (is_ac, battery_percent). battery_percent 0-100 or None."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode != 0:
                return (None, None)
            text = out.stdout or ""
            ac = None
            if "AC Power" in text:
                ac = True
            elif "Battery Power" in text:
                ac = False
            # e.g. "Now drawing from 'Battery Power' -47%; 1:23 remaining"
            m = re.search(r"(\d{1,3})%", text)
            pct = int(m.group(1)) if m else None
            if pct is not None:
                pct = max(0, min(100, pct))
            return (ac, pct)
        if sys.platform == "win32":
            ctypes = __import__("ctypes")
            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte),
                    ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte),
                    ("Reserved1", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]
            status = SYSTEM_POWER_STATUS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
                return (None, None)
            ac = None
            if status.ACLineStatus == 1:
                ac = True
            elif status.ACLineStatus == 0:
                ac = False
            pct = None
            if 0 <= status.BatteryLifePercent <= 100:
                pct = status.BatteryLifePercent
            return (ac, pct)
        if sys.platform.startswith("linux"):
            ps = os.path.join(os.path.sep, "sys", "class", "power_supply")
            if not os.path.isdir(ps):
                return (None, None)
            ac = None
            pct = None
            for name in os.listdir(ps):
                if name.startswith(("AC", "ACAD", "Mains", "ACPI")):
                    online_path = os.path.join(ps, name, "online")
                    if os.path.isfile(online_path):
                        try:
                            with open(online_path) as f:
                                ac = f.read().strip() == "1"
                            break
                        except OSError:
                            pass
            for name in os.listdir(ps):
                if name.startswith(("BAT", "Battery")):
                    cap_path = os.path.join(ps, name, "capacity")
                    if os.path.isfile(cap_path):
                        try:
                            with open(cap_path) as f:
                                pct = int(f.read().strip())
                            pct = max(0, min(100, pct))
                            break
                        except (OSError, ValueError):
                            pass
            return (ac, pct)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return (None, None)


def is_ac_power() -> bool | None:
    """
    True if running on AC power, False if on battery, None if unknown.
    Used to throttle aggressiveness on battery (conserve power / avoid thermal throttling).
    """
    ac, _ = _power_status()
    return ac


def battery_percent() -> int | None:
    """Battery charge 0-100, or None if unknown / no battery."""
    _, pct = _power_status()
    return pct


def default_workers() -> int:
    """Suggested number of workers from CPU count (for crawl / future parallel fetch)."""
    n = os.cpu_count()
    if n is None or n < 1:
        return MIN_WORKERS
    return max(MIN_WORKERS, min(n, MAX_WORKERS))


def detect_hardware() -> dict:
    """
    Detect CPU and (if available) memory. Return a dict with keys like
    cpu_count, workers, memory_gb, safe_asset_workers, safe_head_workers.
    """
    cpu = os.cpu_count()
    if cpu is None or cpu < 1:
        cpu = 1
    workers = max(MIN_WORKERS, min(cpu, MAX_WORKERS))

    out = {
        "cpu_count": cpu,
        "workers": workers,
        "safe_asset_workers": min(SAFE_ASSET_WORKERS, workers),
        "safe_head_workers": min(SAFE_HEAD_WORKERS, workers),
    }

    try:
        import resource
        # Linux/macOS: RSS max (bytes); Windows has no resource
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if soft != resource.RLIM_INFINITY and soft > 0:
            out["memory_limit_bytes"] = soft
    except (ImportError, OSError, AttributeError):
        pass

    # Optional: system memory (platform-specific)
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages is not None and page_size is not None and pages > 0:
            out["memory_gb"] = round((pages * page_size) / (1024**3), 2)
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    return out


def format_hardware(info: dict | None = None) -> str:
    """Return a short human-readable summary of detected hardware and suggested settings."""
    info = info or detect_hardware()
    lines = [
        f"CPU cores: {info.get('cpu_count', '?')}",
        f"Crawl workers: {info.get('workers', '?')}",
        f"Asset workers: {info.get('safe_asset_workers', '?')}",
        f"HEAD workers: {info.get('safe_head_workers', '?')}",
    ]
    if "memory_gb" in info:
        lines.append(f"Memory: {info['memory_gb']} GB")
    ac, pct = _power_status()
    if ac is True:
        lines.append("Power: AC (aggressive crawl allowed)")
    elif ac is False:
        if pct is not None:
            lines.append(f"Power: battery {pct}% (throttled by level)")
        else:
            lines.append("Power: battery (throttled to balanced/conservative)")
    suggested = suggest_aggressiveness(info, _power=(ac, pct))
    lines.append(f"Suggested aggressiveness: {suggested}")
    return "\n".join(lines)


def suggest_aggressiveness(
    hw: dict | None = None,
    _power: tuple[bool | None, int | None] | None = None,
) -> str:
    """
    Suggest an aggressiveness preset from hardware and power.
    Prefer aggressive when on AC with strong hardware; throttle on battery or weak hardware.
    Returns 'conservative', 'balanced', or 'aggressive'.
    """
    hw = hw or detect_hardware()
    cpu = hw.get("cpu_count", 1) or 1
    memory_gb = hw.get("memory_gb") or 0
    ac, pct = _power if _power is not None else _power_status()

    # Weak hardware → always conservative
    if cpu <= 2 or (memory_gb > 0 and memory_gb < 4):
        return "conservative"

    # On battery → throttle by battery %: low = conservative; higher = balanced if hw allows
    if ac is False:
        if pct is not None and pct < BATTERY_LOW_PERCENT:
            return "conservative"
        if pct is not None and pct >= BATTERY_OK_PERCENT and cpu >= 4 and (memory_gb >= 6 or memory_gb == 0):
            return "balanced"
        # Mid range or unknown % or weaker hw → conservative
        return "conservative"

    # AC (or unknown) + strong hardware → aggressive
    if cpu >= 6 and (memory_gb >= 8 or memory_gb == 0):
        return "aggressive"
    return "balanced"


def get_aggressiveness_params(preset: str, hw: dict | None = None) -> dict:
    """
    Return workers and delay for a preset. For 'auto', use suggest_aggressiveness.
    Returns {"workers": int, "delay": float}.
    """
    hw = hw or detect_hardware()
    if preset == "auto":
        preset = suggest_aggressiveness(hw)
    if preset not in AGGRESSIVENESS_PRESETS:
        preset = "balanced"
    p = AGGRESSIVENESS_PRESETS[preset].copy()
    if p.get("workers") is None:
        p["workers"] = max(MIN_WORKERS, min(hw.get("workers", MIN_WORKERS), MAX_WORKERS))
    return p
