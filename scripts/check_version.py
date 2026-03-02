#!/usr/bin/env python3
"""
Verify version consistency: pyproject.toml version matches latest CHANGELOG section.
Run before release or in CI. Exit 0 if consistent, 1 otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_pyproject_version() -> str | None:
    path = PROJECT_ROOT / "pyproject.toml"
    if not path.exists():
        return None
    text = path.read_text()
    m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None


def get_changelog_latest_version() -> str | None:
    path = PROJECT_ROOT / "CHANGELOG.md"
    if not path.exists():
        return None
    text = path.read_text()
    # Match ## [X.Y.Z] - date (first non-Unreleased section)
    for m in re.finditer(r"## \[([^\]]+)\] - \d{4}-\d{2}-\d{2}", text):
        ver = m.group(1)
        if ver.lower() != "unreleased":
            return ver
    return None


def main() -> int:
    pyproject = get_pyproject_version()
    changelog = get_changelog_latest_version()

    if not pyproject:
        print("check_version: no version found in pyproject.toml", file=sys.stderr)
        return 1
    if not changelog:
        print("check_version: no versioned section found in CHANGELOG.md", file=sys.stderr)
        return 1

    if pyproject != changelog:
        print(
            f"check_version: version mismatch — pyproject.toml={pyproject}, CHANGELOG.md={changelog}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
