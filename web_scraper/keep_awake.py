"""Keep system/display awake during long scrapes. Platform-specific."""

import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Generator

# Windows: SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002

# Linux: systemd-inhibit is a system binary (not a Python package).
# Optional install hint when keep-awake is used but binary is missing.
SYSTEMD_INHIBIT_HINT = (
    "Optional: for keep-awake on Linux, install systemd-inhibit "
    "(e.g. sudo apt install systemd, or your distro's systemd package)."
)


@contextmanager
def keep_awake() -> Generator[None, None, None]:
    """
    Context manager that prevents system sleep (and display sleep where supported)
    for the duration of the block. Use for long scrapes.
    """
    proc = None
    try:
        if sys.platform == "darwin":
            # macOS: caffeinate -i (idle) -s (system sleep)
            try:
                proc = subprocess.Popen(
                    ["caffeinate", "-i", "-s"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                pass
        elif sys.platform == "win32":
            try:
                ctypes = __import__("ctypes")
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.SetThreadExecutionState(
                    _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
                )
            except Exception:
                pass
        elif sys.platform.startswith("linux"):
            # Linux: systemd-inhibit if available (releases when process exits)
            if shutil.which("systemd-inhibit"):
                try:
                    proc = subprocess.Popen(
                        [
                            "systemd-inhibit",
                            "--what=sleep:idle",
                            "--who=basic-scraper",
                            "--why=Scraping",
                            "sleep",
                            "infinity",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except (FileNotFoundError, OSError):
                    pass
            else:
                print(SYSTEMD_INHIBIT_HINT, file=sys.stderr)
        yield
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        elif sys.platform == "win32":
            try:
                ctypes = __import__("ctypes")
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            except Exception:
                pass
