"""
Image storage schema detection pipeline.

Detects which image storage schema a page uses (CONTENTdm, NYPL, IIIF manifest,
generic HTML) and runs the appropriate extraction strategy to get full-resolution images.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

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
    extract_iiif_descriptive_manifest,
    image_format_priority,
    parse_iiif_manifest_pages,
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
    manuscript_mode: bool = False  # Preserve manifest order; label all pages in manifest.json
    # IIIF Presentation descriptive data (metadata, summary, …) keyed per manifest URL
    iiif_descriptions: list[dict[str, Any]] = field(default_factory=list)
    # HTML-level meta / OG / JSON-LD snippets from the scraped page
    html_descriptions: list[dict[str, Any]] = field(default_factory=list)


def _record_iiif_description(
    ctx: DiscoveryContext | None,
    manifest_data: dict,
    manifest_url: str | None,
) -> None:
    """Append full IIIF manifest description if not already recorded for this manifest."""
    if ctx is None:
        return
    try:
        page_u = (ctx.url or "").strip() or None
        block = extract_iiif_descriptive_manifest(
            manifest_data,
            manifest_url=manifest_url,
            source_page_url=page_u,
        )
    except Exception:
        return
    key = block.get("manifest_url") or block.get("id")
    existing = {(b.get("manifest_url") or b.get("id")) for b in ctx.iiif_descriptions if isinstance(b, dict)}
    if key and key in existing:
        return
    ctx.iiif_descriptions.append(block)


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


def _page_meta_from_iiif_page(page: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in page.items() if k != "url"}


def _extract_archive_org_entries(
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
    ctx: DiscoveryContext | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    from strigil.archive_org import fetch_image_entries_from_metadata

    out: list[tuple[str, dict[str, Any] | None]] = []
    manifest_urls = find_derived_iiif_manifest_urls(url or "")
    if fetch_manifest and manifest_urls:
        for manifest_url in manifest_urls:
            try:
                raw = fetch_manifest(manifest_url)
                data = json.loads(raw.decode("utf-8"))
                _record_iiif_description(ctx, data, manifest_url)
                for page in parse_iiif_manifest_pages(data, manifest_url=manifest_url):
                    out.append((page["url"], _page_meta_from_iiif_page(page)))
                if out:
                    return out
            except Exception:
                pass
    m = _ARCHIVE_ORG_DETAILS_RE.search(url or "")
    if m:
        for img_url, fname in fetch_image_entries_from_metadata(m.group(1)):
            meta: dict[str, Any] = {"source": "archive_org_metadata"}
            if fname:
                meta["label"] = fname
            out.append((img_url, meta))
    return out


def _extract_wellcome_entries(
    url: str,
    fetch_manifest: Callable[[str], bytes] | None,
    ctx: DiscoveryContext | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    from strigil.adapters.wellcome import WellcomeAdapter

    wa = WellcomeAdapter()
    mu = wa.get_iiif_manifest_url(url, fetch_manifest)
    if not mu or not fetch_manifest:
        return []
    try:
        raw = fetch_manifest(mu)
        data = json.loads(raw.decode("utf-8"))
        _record_iiif_description(ctx, data, mu)
        return [(p["url"], _page_meta_from_iiif_page(p)) for p in parse_iiif_manifest_pages(data, manifest_url=mu)]
    except Exception:
        return []


def _extract_nypl_entries(
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
    ctx: DiscoveryContext | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    out: list[tuple[str, dict[str, Any] | None]] = []
    if fetch_manifest:
        for manifest_url in find_nypl_manifest_urls(url):
            try:
                raw = fetch_manifest(manifest_url)
                data = json.loads(raw.decode("utf-8"))
                _record_iiif_description(ctx, data, manifest_url)
                for page in parse_iiif_manifest_pages(data, manifest_url=manifest_url):
                    out.append((page["url"], _page_meta_from_iiif_page(page)))
            except Exception:
                pass
    if not out:
        for u in find_nypl_iiif_image_urls(html_str):
            out.append((u, None))
    return out


def _extract_iiif_manifest_entries(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    fetch_manifest: Callable[[str], bytes] | None,
    ctx: DiscoveryContext | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    if not fetch_manifest:
        return []
    out: list[tuple[str, dict[str, Any] | None]] = []
    manifest_urls = find_iiif_manifest_urls(soup, url, html_str)
    manifest_urls = list(dict.fromkeys(manifest_urls + find_derived_iiif_manifest_urls(url or "")))
    for manifest_url in manifest_urls:
        try:
            raw = fetch_manifest(manifest_url)
            data = json.loads(raw.decode("utf-8"))
            _record_iiif_description(ctx, data, manifest_url)
            for page in parse_iiif_manifest_pages(data, manifest_url=manifest_url):
                out.append((page["url"], _page_meta_from_iiif_page(page)))
        except Exception:
            pass
    return out


def collect_image_entries(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    *,
    fetch_manifest: Callable[[str], bytes] | None = None,
    limit: int | None = None,
    context: DiscoveryContext | None = None,
) -> list[tuple[str, dict[str, Any] | None]]:
    """
    Like collect_image_urls but returns (image_url, manuscript_meta) per image.
    manuscript_meta includes IIIF canvas labels, manifest URLs, and discovery_order when applicable.
    """
    ctx = context or DiscoveryContext(url=url or "")
    raw: list[tuple[str, dict[str, Any] | None, int]] = []
    seen: set[str] = set()
    disc_seq = 0

    def add(u: str, meta: dict[str, Any] | None = None) -> None:
        nonlocal disc_seq
        if not u or should_skip_image_url(u) or u in seen:
            return
        seen.add(u)
        disc = disc_seq
        disc_seq += 1
        if ctx.manuscript_mode:
            m = dict(meta) if meta else {}
            m["discovery_order"] = disc + 1
            raw.append((u, m, disc))
        elif meta:
            m = dict(meta)
            m["discovery_order"] = disc + 1
            raw.append((u, m, disc))
        else:
            raw.append((u, None, disc))

    def finalize() -> list[tuple[str, dict[str, Any] | None]]:
        if ctx.manuscript_mode:
            ordered = sorted(raw, key=lambda x: x[2])
        else:
            ordered = sorted(raw, key=lambda x: (-image_format_priority(x[0]), x[2]))
        out = [(u, m) for u, m, _ in ordered]
        if limit is not None:
            out = out[:limit]
        return out

    # When source_hint is set, try that adapter first
    if ctx.source_hint:
        from strigil.adapters import ADAPTER_BY_SOURCE

        hint = ctx.source_hint.strip().lower()
        adapter = ADAPTER_BY_SOURCE.get(hint)
        if adapter and adapter.matches(url or ""):
            if hint == "archive_org" and fetch_manifest:
                for u, meta in _extract_archive_org_entries(url, html_str, fetch_manifest, ctx):
                    add(u, meta)
            elif hint == "wellcome" and fetch_manifest:
                for u, meta in _extract_wellcome_entries(url, fetch_manifest, ctx):
                    add(u, meta)
            else:
                for u in adapter.extract_image_urls(url or "", html_str or "", fetch_manifest):
                    add(u, None)
            if raw:
                return finalize()

    for detection in detect_image_schemas(url, soup, html_str):
        schema = detection.schema

        if schema == ImageSchema.GENERIC_HTML:
            for u in find_image_urls(soup, url):
                add(u, None)
            continue

        if schema == ImageSchema.ARCHIVE_ORG:
            for u, meta in _extract_archive_org_entries(url, html_str, fetch_manifest, ctx):
                add(u, meta)
        elif schema == ImageSchema.WELLCOME:
            for u, meta in _extract_wellcome_entries(url, fetch_manifest, ctx):
                add(u, meta)
        elif schema == ImageSchema.CONTENTDM:
            for u in find_contentdm_full_res_urls(url, html_str):
                add(u, None)
        elif schema == ImageSchema.NYPL:
            for u, meta in _extract_nypl_entries(url, html_str, fetch_manifest, ctx):
                add(u, meta)
        elif schema == ImageSchema.HATHITRUST:
            for u in find_hathitrust_imgsrv_urls(html_str, url):
                add(u, None)
        elif schema == ImageSchema.IIIF_MANIFEST:
            for u, meta in _extract_iiif_manifest_entries(soup, url, html_str, fetch_manifest, ctx):
                add(u, meta)
        elif schema in (ImageSchema.EEBO, ImageSchema.ECCO):
            for u in find_image_urls(soup, url):
                add(u, None)

    expected = ctx.expected_images or infer_expected_images(html_str or "")
    if expected and len(raw) < expected * FALLBACK_SHORTFALL_RATIO:
        from strigil.adapters import ALL_ADAPTERS

        for adapter in ALL_ADAPTERS:
            if adapter.matches(url or ""):
                adapter_urls = [
                    u
                    for u in adapter.extract_image_urls(url or "", html_str or "", fetch_manifest)
                    if u and not should_skip_image_url(u)
                ]
                if adapter_urls and len(adapter_urls) > len(raw):
                    new_seen: set[str] = set()
                    new_raw: list[tuple[str, dict[str, Any] | None, int]] = []
                    for i, u in enumerate(dict.fromkeys(adapter_urls)):
                        if not u or should_skip_image_url(u):
                            continue
                        new_seen.add(u)
                        if ctx.manuscript_mode:
                            new_raw.append((u, {"discovery_order": i + 1}, i))
                        else:
                            new_raw.append((u, None, i))
                    raw = new_raw
                    seen = new_seen
                break

    return finalize()


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
    return [u for u, _ in collect_image_entries(
        soup, url, html_str,
        fetch_manifest=fetch_manifest,
        limit=limit,
        context=context,
    )]
