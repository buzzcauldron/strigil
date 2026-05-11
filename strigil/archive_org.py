"""Internet Archive metadata API client for image URL extraction.

Used as fallback when IIIF manifest is unavailable (e.g., raw image collections).
See: https://archive.org/developers/md-read.html
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

# Image formats from metadata API (format field, case-insensitive)
_IMAGE_FORMATS = frozenset(
    {"jpeg", "jpg", "jp2", "jpx", "png", "gif", "tiff", "tif", "webp", "bmp"}
)

# Path substrings that indicate thumbnails/derivatives (skip when full-res exists)
_THUMB_SUBSTRINGS = ("_thumb", "_small", "_medium", "thumb.", ".thumb", "_t.", "_s.")

# Base URL for direct file download
_DOWNLOAD_BASE = "https://archive.org/download"


def fetch_image_urls_from_metadata(identifier: str) -> list[str]:
    """
    Fetch image URLs from Internet Archive metadata API.

    GET https://archive.org/metadata/{identifier} returns a files array.
    Filters for image formats, prefers full-resolution over thumbnails,
    and constructs download URLs.

    :param identifier: IA item identifier (e.g., from archive.org/details/{id})
    :return: List of absolute image URLs, ordered by preference (full-res first)
    """
    url = f"https://archive.org/metadata/{identifier}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    files = data.get("files") or data.get("file") or []
    if isinstance(files, dict):
        files = list(files.values())

    image_files: list[dict] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        fmt = (f.get("format") or "").strip().lower()
        name = (f.get("name") or "").strip()
        if not name:
            continue
        # Check format field
        if fmt and fmt in _IMAGE_FORMATS:
            image_files.append({"name": name, "format": fmt, "size": f.get("size", 0) or 0})
            continue
        # Fallback: check file extension
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _IMAGE_FORMATS:
            image_files.append({"name": name, "format": ext, "size": f.get("size", 0) or 0})

    if not image_files:
        return []

    # Prefer full-resolution; skip thumbnails when a non-thumb version exists
    def is_thumb(n: str) -> bool:
        n_lower = n.lower()
        return any(t in n_lower for t in _THUMB_SUBSTRINGS)

    full_res = [f for f in image_files if not is_thumb(f["name"])]
    use_files = full_res if full_res else image_files

    # Sort by size descending (prefer larger files)
    use_files.sort(key=lambda x: (0 if is_thumb(x["name"]) else 1, -(x["size"] or 0)))

    base = f"{_DOWNLOAD_BASE}/{identifier}/"
    return [base + quote(f["name"], safe="") for f in use_files]


def fetch_image_entries_from_metadata(identifier: str) -> list[tuple[str, str | None]]:
    """
    Like fetch_image_urls_from_metadata but returns (download_url, filename) pairs
    for manuscript labeling when IIIF is unavailable.
    """
    url = f"https://archive.org/metadata/{identifier}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    files = data.get("files") or data.get("file") or []
    if isinstance(files, dict):
        files = list(files.values())

    image_files: list[dict] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        fmt = (f.get("format") or "").strip().lower()
        name = (f.get("name") or "").strip()
        if not name:
            continue
        if fmt and fmt in _IMAGE_FORMATS:
            image_files.append({"name": name, "format": fmt, "size": f.get("size", 0) or 0})
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _IMAGE_FORMATS:
            image_files.append({"name": name, "format": ext, "size": f.get("size", 0) or 0})

    if not image_files:
        return []

    def is_thumb(n: str) -> bool:
        n_lower = n.lower()
        return any(t in n_lower for t in _THUMB_SUBSTRINGS)

    full_res = [f for f in image_files if not is_thumb(f["name"])]
    use_files = full_res if full_res else image_files
    use_files.sort(key=lambda x: (0 if is_thumb(x["name"]) else 1, -(x["size"] or 0)))

    base = f"{_DOWNLOAD_BASE}/{identifier}/"
    return [(base + quote(f["name"], safe=""), f["name"]) for f in use_files]
