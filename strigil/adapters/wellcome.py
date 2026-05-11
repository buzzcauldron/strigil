"""Wellcome Collection adapter: Catalogue API -> IIIF manifest."""

from __future__ import annotations

import json
from typing import Callable

from strigil.extractors import _WELLCOME_WORKS_RE, parse_iiif_manifest

_CATALOGUE_API_BASE = "https://api.wellcomecollection.org/catalogue/v2"


class WellcomeAdapter:
    """Extract images from wellcomecollection.org/works/{id} via Catalogue API + IIIF manifest."""

    def matches(self, url: str) -> bool:
        return bool(_WELLCOME_WORKS_RE.search(url or ""))

    def get_iiif_manifest_url(self, url: str, fetch: Callable[[str], bytes] | None) -> str | None:
        """Resolve the IIIF Presentation manifest URL for a works page."""
        m = _WELLCOME_WORKS_RE.search(url or "")
        if not m or not fetch:
            return None
        return self._get_manifest_url(m.group(1), fetch)

    def extract_image_urls(
        self,
        url: str,
        html: str,
        fetch: Callable[[str], bytes] | None,
    ) -> list[str]:
        manifest_url = self.get_iiif_manifest_url(url, fetch)
        if not manifest_url:
            return []
        try:
            raw = fetch(manifest_url)
            data = json.loads(raw.decode("utf-8"))
            return parse_iiif_manifest(data)
        except Exception:
            return []

    def _get_manifest_url(self, work_id: str, fetch: Callable[[str], bytes]) -> str | None:
        """Fetch Catalogue API and extract IIIF Presentation manifest URL from items."""
        api_url = f"{_CATALOGUE_API_BASE}/works/{work_id}?include=items"
        try:
            raw = fetch(api_url)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        items = data.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            locations = item.get("locations") or []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                if loc.get("locationType", {}).get("id") == "iiif-presentation":
                    url = loc.get("url")
                    if isinstance(url, str) and url.strip():
                        return url.strip().rstrip("/")
        return None
