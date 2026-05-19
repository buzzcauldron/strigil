"""HathiTrust adapter: enumerate full volume via imgsrv after viewer page load."""

from __future__ import annotations

from typing import Callable

from strigil.extractors import find_hathitrust_imgsrv_urls
from strigil.hathitrust import (
    enumerate_hathitrust_volume_urls,
    is_hathitrust_url,
    parse_hathitrust_volume_id,
)


class HathiTrustAdapter:
    """Extract all page images from babel.hathitrust.org/cgi/pt?id=... volumes."""

    def matches(self, url: str) -> bool:
        return is_hathitrust_url(url) and parse_hathitrust_volume_id(url) is not None

    def extract_image_urls(
        self,
        url: str,
        html: str,
        fetch: Callable[[str], bytes] | None,
    ) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        def add(u: str) -> None:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)

        # Full volume enumeration (page count from HTML or probe)
        def image_exists(img_url: str) -> bool:
            if not fetch:
                return False
            try:
                raw = fetch(img_url)
                return bool(raw) and len(raw) > 500
            except Exception:
                return False

        for u in enumerate_hathitrust_volume_urls(
            url,
            html or "",
            image_exists=image_exists if fetch else None,
        ):
            add(u)

        # Also pick up any imgsrv URLs embedded in HTML (current page thumbnails)
        for u in find_hathitrust_imgsrv_urls(html or "", url):
            add(u)

        return urls
