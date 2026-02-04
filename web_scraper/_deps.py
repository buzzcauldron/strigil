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

# (import_name, pip_package_name); order preserved for install
OPTIONAL = [
    ("playwright", "playwright"),
    ("tqdm", "tqdm"),
    ("readability", "readability-lxml"),
]

INSTALL_CMD = "pip install basic-scraper"
INSTALL_CMD_SOURCE = "pip install -e ."
OPTIONAL_EXTRAS = "pip install basic-scraper[js,progress,readability]"


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


def _try_auto_install_optional() -> None:
    """If AUTO_INSTALL_ENV is set, install any missing optional deps and continue (no exit)."""
    if os.environ.get(AUTO_INSTALL_ENV, "").lower() not in ("1", "true", "yes"):
        return
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return
    print("Auto-installing optional dependencies...", file=sys.stderr)
    cmd = [sys.executable, "-m", "pip", "install", "-q"] + missing
    try:
        subprocess.run(cmd, check=True)
        # If we installed playwright, install browser binaries
        if "playwright" in missing:
            try:
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install"],
                    capture_output=True,
                    timeout=300,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                print("Playwright browsers: run 'playwright install' if you need JS rendering.", file=sys.stderr)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Auto-install optional failed: {e}. Install manually: {OPTIONAL_EXTRAS}", file=sys.stderr)


def ensure_optional() -> None:
    """If AUTO_INSTALL_ENV is set, install any missing optional deps (no exit). Call after check_required()."""
    _try_auto_install_optional()


def optional_hint() -> str | None:
    """Return a one-line hint if any optional deps are missing, else None."""
    missing = [pip_name for mod_name, pip_name in OPTIONAL if not _import(mod_name)]
    if not missing:
        return None
    return f"Optional: {OPTIONAL_EXTRAS} for JS rendering, progress bar, and readability."
