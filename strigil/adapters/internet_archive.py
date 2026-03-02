"""Internet Archive adapter: IIIF manifest + metadata API fallback."""

from __future__ import annotations

import json
from typing import Callable

from strigil.extractors import (
    _ARCHIVE_ORG_DETAILS_RE,
    find_derived_iiif_manifest_urls,
    parse_iiif_manifest,
)


class InternetArchiveAdapter:
    """Extract images from archive.org/details/{id} via IIIF or metadata API."""

    def matches(self, url: str) -> bool:
        return bool(_ARCHIVE_ORG_DETAILS_RE.search(url or ""))

    def extract_image_urls(
        self,
        url: str,
        html: str,
        fetch: Callable[[str], bytes] | None,
    ) -> list[str]:
        urls: list[str] = []
        # 1. Try IIIF manifest (derived from URL)
        manifest_urls = find_derived_iiif_manifest_urls(url or "")
        if fetch and manifest_urls:
            for manifest_url in manifest_urls:
                try:
                    raw = fetch(manifest_url)
                    data = json.loads(raw.decode("utf-8"))
                    urls.extend(parse_iiif_manifest(data))
                    if urls:
                        return urls
                except Exception:
                    pass
        # 2. Fallback: metadata API
        try:
            from strigil.archive_org import fetch_image_urls_from_metadata

            m = _ARCHIVE_ORG_DETAILS_RE.search(url or "")
            if m:
                urls = fetch_image_urls_from_metadata(m.group(1))
        except ImportError:
            pass
        except Exception:
            pass
        return urls
