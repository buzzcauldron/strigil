"""Dependency checks: auto-install missing deps on first run, or prompt if pip unavailable."""

import os
import subprocess
import sys

# Set to "0" or "false" to disable auto-install
AUTO_INSTALL_ENV = "STRIGIL_AUTO_INSTALL_DEPS"

REQUIRED = [
    ("httpx", "httpx"),
    ("bs4", "beautifulsoup4"),
    ("lxml", "lxml"),
]

# (import_name, pip_package_name); order preserved for install
OPTIONAL = [
    ("readability", "readability-lxml"),
    ("tqdm", "tqdm"),
]

INSTALL_CMD = "pip install strigil"
INSTALL_CMD_SOURCE = "pip install -e ."
OPTIONAL_EXTRAS = "pip install strigil[js,readability]"


def _auto_install_enabled() -> bool:
    """True if auto-install is enabled (default: yes)."""
    val = os.environ.get(AUTO_INSTALL_ENV, "1").lower()
    return val not in ("0", "false", "no")


def _import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _try_auto_install(missing: list[str]) -> bool:
    """If auto-install enabled, run pip install and exit 0. Returns True if we installed and exited."""
    if not _auto_install_enabled():
        return False
    cmd = [sys.executable, "-m", "pip", "install", "-q"] + missing
    print("Auto-installing dependencies...", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True)
        print("Dependencies installed. Run the command again.", file=sys.stderr)
        sys.exit(0)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Auto-install failed: {e}. Install manually.", file=sys.stderr)
        sys.exit(1)


def check_required() -> bool:
    """Verify required dependencies are importable. On failure, auto-install and exit, or print message and exit."""
    missing = [pip_name for mod_name, pip_name in REQUIRED if not _import(mod_name)]
    if not missing:
        return True
    _try_auto_install(missing)
    print("Missing required dependencies.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Install from PyPI:", file=sys.stderr)
    print(f"    {INSTALL_CMD}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Or install from source (project directory):", file=sys.stderr)
    print(f"    {INSTALL_CMD_SOURCE}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Or enable auto-install (default) and re-run:", file=sys.stderr)
    print(f"    strigil --url ...  # {AUTO_INSTALL_ENV}=0 to disable", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Missing:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)


def _try_auto_install_optional() -> None:
    """If auto-install enabled, install any missing optional deps and continue (no exit)."""
    if not _auto_install_enabled():
        return
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return
    print("Auto-installing optional dependencies...", file=sys.stderr)
    cmd = [sys.executable, "-m", "pip", "install", "-q"] + missing
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Auto-install optional failed: {e}. Install manually: {OPTIONAL_EXTRAS}", file=sys.stderr)


def ensure_optional() -> None:
    """If auto-install enabled, install any missing optional deps (no exit). Call after check_required()."""
    _try_auto_install_optional()


def optional_hint() -> str | None:
    """Return a one-line hint if any optional deps are missing, else None."""
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return None
    return f"Optional: {OPTIONAL_EXTRAS} for improved text extraction."
