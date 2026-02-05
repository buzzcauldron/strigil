"""
Image URL discovery: one place that combines all sources (CONTENTdm, IIIF, in-page).
Depends only on extractors and an optional fetch callback—no Fetcher type.
"""

import json
from typing import Callable

from bs4 import BeautifulSoup

from web_scraper.extractors import (
    find_contentdm_full_res_urls,
    find_image_urls,
    find_iiif_manifest_urls,
    parse_iiif_manifest,
    should_skip_image_url,
)


def collect_image_urls(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    *,
    fetch_manifest: Callable[[str], bytes] | None = None,
    limit: int | None = None,
) -> list[str]:
    """
    Collect image URLs from CONTENTdm, IIIF manifests, and page HTML.
    Deduped and skip-filtered. Optional fetch_manifest(manifest_url) for IIIF.
    """
    img_urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and not should_skip_image_url(u) and u not in seen:
            seen.add(u)
            img_urls.append(u)

    for u in find_contentdm_full_res_urls(url, html_str):
        add(u)

    if fetch_manifest is not None:
        for manifest_url in find_iiif_manifest_urls(soup, url, html_str):
            try:
                raw = fetch_manifest(manifest_url)
                data = json.loads(raw.decode("utf-8"))
                for u in parse_iiif_manifest(data):
                    add(u)
            except Exception:
                pass

    for u in find_image_urls(soup, url):
        add(u)

    if limit is not None:
        img_urls = img_urls[:limit]
    return img_urls
