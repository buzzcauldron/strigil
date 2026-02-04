"""Dependency checks: prompt user to install if required deps are missing."""

import os
import subprocess
import sys

AUTO_INSTALL_ENV = "BASIC_SCRAPER_AUTO_INSTALL_DEPS"

REQUIRED = [
    ("httpx", "httpx"),
    ("bs4", "beautifulsoup4"),
    ("lxml", "lxml"),
]

OPTIONAL = [
    ("playwright", "playwright"),
]

INSTALL_CMD = "pip install basic-scraper"
INSTALL_CMD_SOURCE = "pip install -e ."
OPTIONAL_EXTRAS = "pip install basic-scraper[js]"


def _import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _try_auto_install(missing: list[str]) -> bool:
    """If AUTO_INSTALL_ENV is set, run pip install and exit 0. Returns True if we installed and exited."""
    if os.environ.get(AUTO_INSTALL_ENV, "").lower() not in ("1", "true", "yes"):
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
    """Verify required dependencies are importable. On failure, print message and exit (or auto-install if env set)."""
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
    print("  Or set env to auto-install and re-run:", file=sys.stderr)
    print(f"    {AUTO_INSTALL_ENV}=1 scrape --url ...", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Missing:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)


def optional_hint() -> str | None:
    """Return a one-line hint if any optional deps are missing, else None."""
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return None
    return f"Optional: {OPTIONAL_EXTRAS} for JS rendering of heavy pages."
