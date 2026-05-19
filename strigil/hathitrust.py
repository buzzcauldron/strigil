"""HathiTrust babel/imgsrv: full-resolution URLs and volume enumeration."""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import parse_qs, urlparse

_HATHITRUST_PT_RE = re.compile(
    r"(?:babel\.)?hathitrust\.org/cgi/pt\?id=([^;&\s]+)",
    re.IGNORECASE,
)

# Wikisource: size=10000 requests maximum scan resolution (not pixel count).
HATHITRUST_FULL_SIZE = "10000"
HATHITRUST_IMGSRV_BASE = "https://babel.hathitrust.org/cgi/imgsrv/image"
DEFAULT_MAX_PAGES = 3000


def is_hathitrust_url(url: str) -> bool:
    """True if URL is a HathiTrust viewer or imgsrv endpoint."""
    if not url:
        return False
    host = (urlparse(url).netloc or "").lower()
    return "hathitrust.org" in host


def parse_hathitrust_volume_id(page_url: str) -> str | None:
    """Extract volume id from pt?id=... or imgsrv URLs."""
    m = _HATHITRUST_PT_RE.search(page_url or "")
    if m:
        return m.group(1)
    parsed = urlparse(page_url or "")
    if "hathitrust.org" not in (parsed.netloc or "").lower():
        return None
    query = (parsed.query or "").replace(";", "&")
    qs = parse_qs(query)
    ids = qs.get("id")
    return ids[0] if ids else None


def hathitrust_image_url(vol_id: str, seq: int, *, size: str = HATHITRUST_FULL_SIZE) -> str:
    """Build full-resolution imgsrv URL (semicolon-separated query, per HathiTrust convention)."""
    return f"{HATHITRUST_IMGSRV_BASE}?id={vol_id};seq={seq};size={size};rotation=0"


def infer_hathitrust_page_count(html_str: str, page_url: str = "") -> int | None:
    """Infer total pages from viewer HTML or embedded JSON."""
    if not html_str:
        return None
    patterns = (
        r'"numPages"\s*:\s*(\d+)',
        r'"pageCount"\s*:\s*(\d+)',
        r'"totalPages"\s*:\s*(\d+)',
        r'"lastIndex"\s*:\s*(\d+)',
        r"of\s+(\d+)\s+pages",
        r"Page\s+\d+\s+of\s+(\d+)",
        r"(\d+)\s+pages?\s+total",
    )
    for pat in patterns:
        m = re.search(pat, html_str, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if n > 0:
                return n
    seqs = [int(x) for x in re.findall(r"[;?&]seq=(\d+)", html_str)]
    if seqs:
        return max(seqs)
    from strigil.extractors import infer_expected_images

    return infer_expected_images(html_str)


def probe_hathitrust_page_count(
    vol_id: str,
    image_exists: Callable[[str], bool],
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> int:
    """Binary-search for the last seq that returns a valid image (needs authenticated fetch)."""
    lo, hi = 1, max_pages
    last_ok = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if image_exists(hathitrust_image_url(vol_id, mid)):
            last_ok = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return last_ok


def enumerate_hathitrust_volume_urls(
    page_url: str,
    html_str: str = "",
    *,
    image_exists: Callable[[str], bool] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[str]:
    """
    Return full-resolution image URLs for every page in a HathiTrust volume.
    Uses HTML page count when available; optional image_exists probe (browser/HEAD).
    """
    vol_id = parse_hathitrust_volume_id(page_url)
    if not vol_id:
        return []

    count = infer_hathitrust_page_count(html_str, page_url)
    if count is None and image_exists is not None:
        count = probe_hathitrust_page_count(vol_id, image_exists, max_pages=max_pages)
    if count is None or count < 1:
        # Current page only
        parsed = urlparse(page_url)
        query = (parsed.query or "").replace(";", "&")
        qs = parse_qs(query)
        seq = int(qs.get("seq", ["1"])[0])
        return [hathitrust_image_url(vol_id, seq)]

    return [hathitrust_image_url(vol_id, seq) for seq in range(1, count + 1)]
