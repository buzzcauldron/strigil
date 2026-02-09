"""
Image storage schema detection pipeline.

Detects which image storage schema a page uses (CONTENTdm, NYPL, IIIF manifest,
generic HTML) and runs the appropriate extraction strategy to get full-resolution images.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from bs4 import BeautifulSoup

from strigil.extractors import (
    find_contentdm_full_res_urls,
    find_image_urls,
    find_iiif_manifest_urls,
    find_nypl_iiif_image_urls,
    find_nypl_manifest_urls,
    parse_iiif_manifest,
    should_skip_image_url,
)

# Detection patterns (mirrored from extractors for schema detection)
_CONTENTDM_ITEM_RE = re.compile(
    r"/digital/collection/([^/?#]+)/id/(\d+)",
    re.IGNORECASE,
)
_IIIF_IMAGE_API_RE = re.compile(
    r"(https?://[^/]+/digital/iiif/2/[^/]+)/full/[^/]+/\d+/[^/]+\.(jpg|png|webp)",
    re.IGNORECASE,
)
_NYPL_ITEMS_RE = re.compile(
    r"^https?://(?:www\.)?digitalcollections\.nypl\.org/items/[a-f0-9-]{36}",
    re.IGNORECASE,
)


class ImageSchema(str, Enum):
    """Identifies the image storage schema used by a page."""

    CONTENTDM = "contentdm"  # OCLC CONTENTdm IIIF
    NYPL = "nypl"  # NYPL Digital Collections (manifest at api-collections)
    IIIF_MANIFEST = "iiif_manifest"  # Generic IIIF (manifest in iframe/link)
    GENERIC_HTML = "generic_html"  # Standard img, srcset, data-src, etc.


@dataclass
class DetectionResult:
    """Result of schema detection: schema type and confidence (0–1)."""

    schema: ImageSchema
    confidence: float


def detect_image_schemas(
    url: str,
    soup: BeautifulSoup,
    html_str: str,
) -> list[DetectionResult]:
    """
    Detect which image storage schemas apply to this page.
    Returns schemas in priority order (highest confidence first).
    """
    results: list[DetectionResult] = []
    seen: set[ImageSchema] = set()

    # NYPL: URL pattern is definitive
    if _NYPL_ITEMS_RE.match(url):
        results.append(DetectionResult(ImageSchema.NYPL, 1.0))
        seen.add(ImageSchema.NYPL)

    # CONTENTdm: URL pattern or IIIF Image API URLs in HTML
    if ImageSchema.CONTENTDM not in seen:
        if _CONTENTDM_ITEM_RE.search(url or ""):
            results.append(DetectionResult(ImageSchema.CONTENTDM, 0.95))
            seen.add(ImageSchema.CONTENTDM)
        elif html_str and _IIIF_IMAGE_API_RE.search(html_str):
            results.append(DetectionResult(ImageSchema.CONTENTDM, 0.8))
            seen.add(ImageSchema.CONTENTDM)

    # IIIF manifest: manifest URLs found in page (skip if NYPL - we use NYPL path)
    if ImageSchema.IIIF_MANIFEST not in seen and ImageSchema.NYPL not in seen:
        manifest_urls = find_iiif_manifest_urls(soup, url, html_str)
        if manifest_urls:
            results.append(DetectionResult(ImageSchema.IIIF_MANIFEST, 0.9))
            seen.add(ImageSchema.IIIF_MANIFEST)

    # Generic HTML: always applicable as fallback
    results.append(DetectionResult(ImageSchema.GENERIC_HTML, 0.5))
    seen.add(ImageSchema.GENERIC_HTML)

    return results


def _extract_contentdm(
    url: str,
    html_str: str,
) -> list[str]:
    """Extract image URLs using CONTENTdm IIIF."""
    return find_contentdm_full_res_urls(url, html_str)


def _extract_nypl(
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
) -> list[str]:
    """Extract image URLs using NYPL manifest + IIIF 3."""
    urls: list[str] = []
    # Prefer manifest (gets all canvases)
    if fetch_manifest:
        for manifest_url in find_nypl_manifest_urls(url):
            try:
                raw = fetch_manifest(manifest_url)
                data = json.loads(raw.decode("utf-8"))
                urls.extend(parse_iiif_manifest(data))
            except Exception:
                pass
    if not urls:
        urls = find_nypl_iiif_image_urls(html_str)
    return urls


def _extract_iiif_manifest(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
) -> list[str]:
    """Extract image URLs from IIIF manifest(s)."""
    if not fetch_manifest:
        return []
    urls: list[str] = []
    for manifest_url in find_iiif_manifest_urls(soup, url, html_str):
        try:
            raw = fetch_manifest(manifest_url)
            data = json.loads(raw.decode("utf-8"))
            urls.extend(parse_iiif_manifest(data))
        except Exception:
            pass
    return urls


def _extract_generic_html(soup: BeautifulSoup, url: str) -> list[str]:
    """Extract image URLs from standard HTML elements."""
    return find_image_urls(soup, url)


def collect_image_urls(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    *,
    fetch_manifest: Callable[[str], bytes] | None = None,
    limit: int | None = None,
) -> list[str]:
    """
    Detect image storage schema and extract URLs using the appropriate strategy.
    Runs schema-specific extractors in priority order, dedupes, and optionally limits.
    """
    img_urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and not should_skip_image_url(u) and u not in seen:
            seen.add(u)
            img_urls.append(u)

    # Skip generic HTML if we have a richer schema that yielded results
    ran_rich_schema = False
    rich_results = 0

    for detection in detect_image_schemas(url, soup, html_str):
        schema = detection.schema

        if schema == ImageSchema.GENERIC_HTML:
            # Run generic only if no rich schema produced enough, or always as supplement
            if ran_rich_schema and rich_results >= 3:
                # Rich schema found plenty; still add generic for any extras (e.g. cover image)
                for u in _extract_generic_html(soup, url):
                    add(u)
            else:
                for u in _extract_generic_html(soup, url):
                    add(u)
            continue

        ran_rich_schema = True
        before = len(img_urls)

        if schema == ImageSchema.CONTENTDM:
            for u in _extract_contentdm(url, html_str):
                add(u)
        elif schema == ImageSchema.NYPL:
            for u in _extract_nypl(url, html_str, fetch_manifest):
                add(u)
        elif schema == ImageSchema.IIIF_MANIFEST:
            for u in _extract_iiif_manifest(soup, url, html_str, fetch_manifest):
                add(u)

        rich_results += len(img_urls) - before

    if limit is not None:
        img_urls = img_urls[:limit]
    return img_urls
