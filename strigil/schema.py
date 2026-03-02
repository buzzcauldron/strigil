"""
Image storage schema detection pipeline.

Detects which image storage schema a page uses (CONTENTdm, NYPL, IIIF manifest,
generic HTML) and runs the appropriate extraction strategy to get full-resolution images.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from bs4 import BeautifulSoup

from strigil.extractors import (
    _ARCHIVE_ORG_DETAILS_RE,
    _CONTENTDM_ITEM_RE,
    _WELLCOME_WORKS_RE,
    infer_expected_images,
    _ECCO_DOMAIN_RE,
    _EEBO_DOMAIN_RE,
    _HATHITRUST_IMGSRV_RE,
    _HATHITRUST_PT_RE,
    _IIIF_IMAGE_API_RE,
    _NYPL_ITEMS_RE,
    find_contentdm_full_res_urls,
    find_derived_iiif_manifest_urls,
    find_hathitrust_imgsrv_urls,
    find_image_urls,
    find_iiif_manifest_urls,
    find_nypl_iiif_image_urls,
    find_nypl_manifest_urls,
    image_format_priority,
    parse_iiif_manifest,
    should_skip_image_url,
)


class ImageSchema(str, Enum):
    """Identifies the image storage schema used by a page."""

    ARCHIVE_ORG = "archive_org"  # Internet Archive (IIIF derived + metadata API fallback)
    WELLCOME = "wellcome"  # Wellcome Collection (Catalogue API -> IIIF manifest)
    CONTENTDM = "contentdm"  # OCLC CONTENTdm IIIF
    NYPL = "nypl"  # NYPL Digital Collections (manifest at api-collections)
    HATHITRUST = "hathitrust"  # HathiTrust babel/imgsrv
    IIIF_MANIFEST = "iiif_manifest"  # Generic IIIF (manifest in iframe/link)
    EEBO = "eebo"  # Early English Books Online (ProQuest)
    ECCO = "ecco"  # Eighteenth Century Collections Online (Gale)
    GENERIC_HTML = "generic_html"  # Standard img, srcset, data-src, etc.


@dataclass
class DetectionResult:
    """Result of schema detection: schema type and confidence (0–1)."""

    schema: ImageSchema
    confidence: float


# Fallback threshold: try adapter when found < expected * FALLBACK_SHORTFALL_RATIO
FALLBACK_SHORTFALL_RATIO = 0.5


@dataclass
class DiscoveryContext:
    """Optional hints for image discovery and fallback strategies."""

    expected_images: int | None = None  # User hint: we expect ~N images
    source_hint: str | None = None  # User hint: force adapter (e.g. "wellcome", "archive_org")
    url: str = ""


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

    # Internet Archive: archive.org/details/{id} -> IIIF manifest or metadata API
    if ImageSchema.ARCHIVE_ORG not in seen and _ARCHIVE_ORG_DETAILS_RE.search(url or ""):
        results.append(DetectionResult(ImageSchema.ARCHIVE_ORG, 0.95))
        seen.add(ImageSchema.ARCHIVE_ORG)

    # Wellcome Collection: wellcomecollection.org/works/{id} -> Catalogue API + IIIF manifest
    if ImageSchema.WELLCOME not in seen and _WELLCOME_WORKS_RE.search(url or ""):
        results.append(DetectionResult(ImageSchema.WELLCOME, 0.95))
        seen.add(ImageSchema.WELLCOME)

    # CONTENTdm: URL pattern or IIIF Image API URLs in HTML
    if ImageSchema.CONTENTDM not in seen:
        if _CONTENTDM_ITEM_RE.search(url or ""):
            results.append(DetectionResult(ImageSchema.CONTENTDM, 0.95))
            seen.add(ImageSchema.CONTENTDM)
        elif html_str and _IIIF_IMAGE_API_RE.search(html_str):
            results.append(DetectionResult(ImageSchema.CONTENTDM, 0.8))
            seen.add(ImageSchema.CONTENTDM)

    # HathiTrust: babel.hathitrust.org/cgi/pt or imgsrv URLs in HTML
    if ImageSchema.HATHITRUST not in seen:
        if _HATHITRUST_PT_RE.search(url or ""):
            results.append(DetectionResult(ImageSchema.HATHITRUST, 0.95))
            seen.add(ImageSchema.HATHITRUST)
        elif html_str and _HATHITRUST_IMGSRV_RE.search(html_str):
            results.append(DetectionResult(ImageSchema.HATHITRUST, 0.85))
            seen.add(ImageSchema.HATHITRUST)

    # EEBO (ProQuest): eebo.proquest.com, search.proquest.com, eebo.chadwyck.com
    if ImageSchema.EEBO not in seen and _EEBO_DOMAIN_RE.search(url or ""):
        results.append(DetectionResult(ImageSchema.EEBO, 0.9))
        seen.add(ImageSchema.EEBO)

    # ECCO (Gale): link.gale.com/apps/ECCO
    if ImageSchema.ECCO not in seen and _ECCO_DOMAIN_RE.search(url or ""):
        results.append(DetectionResult(ImageSchema.ECCO, 0.9))
        seen.add(ImageSchema.ECCO)

    # IIIF manifest: manifest URLs from HTML or derived from URL (archive.org, Bodleian, Stanford)
    if ImageSchema.IIIF_MANIFEST not in seen and ImageSchema.NYPL not in seen:
        manifest_urls = find_iiif_manifest_urls(soup, url, html_str)
        manifest_urls = list(dict.fromkeys(manifest_urls + find_derived_iiif_manifest_urls(url or "")))
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
    """Extract image URLs from IIIF manifest(s). Uses HTML-found and derived manifest URLs."""
    if not fetch_manifest:
        return []
    manifest_urls = find_iiif_manifest_urls(soup, url, html_str)
    manifest_urls = list(dict.fromkeys(manifest_urls + find_derived_iiif_manifest_urls(url or "")))
    urls: list[str] = []
    for manifest_url in manifest_urls:
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


def _extract_hathitrust(url: str, html_str: str) -> list[str]:
    """Extract image URLs using HathiTrust imgsrv (full-res from thumbnails)."""
    return find_hathitrust_imgsrv_urls(html_str, url)


def _extract_archive_org(
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
) -> list[str]:
    """Extract image URLs from Internet Archive via adapter (IIIF + metadata API fallback)."""
    from strigil.adapters import ALL_ADAPTERS

    for adapter in ALL_ADAPTERS:
        if adapter.matches(url or ""):
            return adapter.extract_image_urls(url or "", html_str or "", fetch_manifest)
    return []


def _extract_wellcome(
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
) -> list[str]:
    """Extract image URLs from Wellcome Collection via adapter (Catalogue API -> IIIF manifest)."""
    from strigil.adapters import ALL_ADAPTERS

    for adapter in ALL_ADAPTERS:
        if adapter.matches(url or ""):
            return adapter.extract_image_urls(url or "", html_str or "", fetch_manifest)
    return []


def collect_image_urls(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    *,
    fetch_manifest: Callable[[str], bytes] | None = None,
    limit: int | None = None,
    context: DiscoveryContext | None = None,
) -> list[str]:
    """
    Detect image storage schema and extract URLs using the appropriate strategy.
    Runs schema-specific extractors in priority order, dedupes, and optionally limits.
    When context.source_hint is set, tries that adapter first.
    """
    img_urls: list[str] = []
    seen: set[str] = set()
    ctx = context or DiscoveryContext(url=url or "")

    def add(u: str) -> None:
        if u and not should_skip_image_url(u) and u not in seen:
            seen.add(u)
            img_urls.append(u)

    # When source_hint is set, try that adapter first
    if ctx.source_hint:
        from strigil.adapters import ADAPTER_BY_SOURCE

        hint = ctx.source_hint.strip().lower()
        adapter = ADAPTER_BY_SOURCE.get(hint)
        if adapter and adapter.matches(url or ""):
            for u in adapter.extract_image_urls(url or "", html_str or "", fetch_manifest):
                add(u)
            if img_urls:
                img_urls.sort(key=lambda u: -image_format_priority(u))
                if limit is not None:
                    img_urls = img_urls[:limit]
                return img_urls

    for detection in detect_image_schemas(url, soup, html_str):
        schema = detection.schema

        if schema == ImageSchema.GENERIC_HTML:
            for u in _extract_generic_html(soup, url):
                add(u)
            continue

        if schema == ImageSchema.ARCHIVE_ORG:
            for u in _extract_archive_org(url, html_str, fetch_manifest):
                add(u)
        elif schema == ImageSchema.WELLCOME:
            for u in _extract_wellcome(url, html_str, fetch_manifest):
                add(u)
        elif schema == ImageSchema.CONTENTDM:
            for u in _extract_contentdm(url, html_str):
                add(u)
        elif schema == ImageSchema.NYPL:
            for u in _extract_nypl(url, html_str, fetch_manifest):
                add(u)
        elif schema == ImageSchema.HATHITRUST:
            for u in _extract_hathitrust(url, html_str):
                add(u)
        elif schema == ImageSchema.IIIF_MANIFEST:
            for u in _extract_iiif_manifest(soup, url, html_str, fetch_manifest):
                add(u)
        elif schema in (ImageSchema.EEBO, ImageSchema.ECCO):
            for u in _extract_generic_html(soup, url):
                add(u)

    # Fallback: when expected_images (user or inferred) suggests we should have more, try adapters
    expected = ctx.expected_images or infer_expected_images(html_str or "")
    if expected and len(img_urls) < expected * FALLBACK_SHORTFALL_RATIO:
        from strigil.adapters import ALL_ADAPTERS

        for adapter in ALL_ADAPTERS:
            if adapter.matches(url or ""):
                adapter_urls = [
                    u for u in adapter.extract_image_urls(url or "", html_str or "", fetch_manifest)
                    if u and not should_skip_image_url(u)
                ]
                if adapter_urls and len(adapter_urls) > len(img_urls):
                    img_urls = list(dict.fromkeys(adapter_urls))
                break

    # Prioritize JPEG/TIFF (archival quality) before applying limit
    img_urls.sort(key=lambda u: -image_format_priority(u))
    if limit is not None:
        img_urls = img_urls[:limit]
    return img_urls
