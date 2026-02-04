"""Dependency checks: prompt user to install if required deps are missing."""

import sys

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


def check_required() -> bool:
    """Verify required dependencies are importable. On failure, print message and exit."""
    missing = [pip_name for mod_name, pip_name in REQUIRED if not _import(mod_name)]
    if not missing:
        return True
    print("Missing required dependencies.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Install from PyPI:", file=sys.stderr)
    print(f"    {INSTALL_CMD}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Or install from source (project directory):", file=sys.stderr)
    print(f"    {INSTALL_CMD_SOURCE}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Missing:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)


def optional_hint() -> str | None:
    """Return a one-line hint if any optional deps are missing, else None."""
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return None
    return f"Optional: {OPTIONAL_EXTRAS} for JS rendering of heavy pages."
