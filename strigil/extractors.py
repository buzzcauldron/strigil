"""Extract PDF links, main text, and image URLs from HTML."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

# Match manifest= URL in iframe src/hash (e.g. uv.html#?manifest=https://.../manifest.json)
_IIIF_MANIFEST_RE = re.compile(
    r"manifest=([^&\s'\"]+manifest\.json)",
    re.IGNORECASE,
)
# Match manifest.json URLs in href or plain text
_IIIF_MANIFEST_URL_RE = re.compile(
    r"https?://[^\s'\"<>]+manifest\.json(?:\?[^\s'\"]*)?",
    re.IGNORECASE,
)
# CONTENTdm item page: /digital/collection/{coll}/id/{id}
_CONTENTDM_ITEM_RE = re.compile(
    r"/digital/collection/([^/?#]+)/id/(\d+)",
    re.IGNORECASE,
)
# Digital Bodleian: digital.bodleian.ox.ac.uk/objects/{uuid}/... (IIIF at iiif.bodleian.ox.ac.uk)
_BODLEIAN_OBJECT_RE = re.compile(
    r"digital\.bodleian\.ox\.ac\.uk/objects/([a-f0-9-]{36})",
    re.IGNORECASE,
)
# Internet Archive: archive.org/details/{identifier}/... -> iiif.archive.org/iiif/{id}/manifest.json
_ARCHIVE_ORG_DETAILS_RE = re.compile(
    r"archive\.org/details/([^/?#]+)",
    re.IGNORECASE,
)
# Stanford PURL: purl.stanford.edu/{id}/... -> purl.stanford.edu/{id}/iiif/manifest
_STANFORD_PURL_RE = re.compile(
    r"purl\.stanford\.edu/([a-z0-9_-]+)",
    re.IGNORECASE,
)
# Wellcome Collection: wellcomecollection.org/works/{id} -> Catalogue API + IIIF manifest
_WELLCOME_WORKS_RE = re.compile(
    r"wellcomecollection\.org/works/([a-z0-9_-]+)",
    re.IGNORECASE,
)
# HathiTrust: babel.hathitrust.org/cgi/pt?id=xxx or cgi/imgsrv/...
_HATHITRUST_PT_RE = re.compile(
    r"(?:babel\.)?hathitrust\.org/cgi/pt\?id=([^;&\s]+)",
    re.IGNORECASE,
)
_HATHITRUST_IMGSRV_RE = re.compile(
    r"(https?://(?:babel\.)?hathitrust\.org/cgi/imgsrv/(?:image|thumbnail)\?[^&\s]+)",
    re.IGNORECASE,
)
# EEBO (ProQuest): eebo.proquest.com, search.proquest.com/eebo, eebo.chadwyck.com,
# and the modern portal whose EEBO docs live under www.proquest.com/eebo/docview/...
_EEBO_DOMAIN_RE = re.compile(
    r"(?:eebo\.proquest\.com|search\.proquest\.com|eebo\.chadwyck\.com|"
    r"(?:www\.)?proquest\.com/eebo[/?])",
    re.IGNORECASE,
)
# ECCO (Gale): link.gale.com/apps/ECCO and go.gale.com/ps/...?p=ECCO
_ECCO_DOMAIN_RE = re.compile(
    r"(?:link\.gale\.com/apps/ECCO|gale\.com/(?:apps/ECCO|ps/).*ECCO)",
    re.IGNORECASE,
)
# IIIF Image API URL with size/region (e.g. /full/pct:15/) -> we want full size (full/full; max not supported on all servers)
_IIIF_IMAGE_API_RE = re.compile(
    r"(https?://[^/]+/digital/iiif/2/[^/]+)/full/[^/]+/\d+/[^/]+\.(jpg|png|webp)",
    re.IGNORECASE,
)
# NYPL Digital Collections: /items/{uuid} (JS-heavy, manifest at api-collections)
_NYPL_ITEMS_RE = re.compile(
    r"^https?://(?:www\.)?digitalcollections\.nypl\.org/items/[a-f0-9-]{36}",
    re.IGNORECASE,
)
# NYPL IIIF 3 image URLs: iiif.nypl.org/iiif/3/{id}/full/{size}/0/default.jpg -> full size
_NYPL_IIIF3_RE = re.compile(
    r"(https?://iiif\.nypl\.org/iiif/3/[a-f0-9]+)/full/[^/]+/\d+/[^/]+\.(jpg|png|webp)",
    re.IGNORECASE,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tif", ".tiff"}


def image_format_priority(url: str) -> int:
    """Return priority for image format (higher = preferred). JPEG/TIFF first, then JP2, then others."""
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".tif", ".tiff")):
        return 2
    if path.endswith(".jp2") or path.endswith(".jpx"):
        return 1
    return 0

# URL path patterns to skip (UI chrome: favicons, banners, logos, social icons, etc.)
SKIP_IMAGE_PATTERNS = (
    "/favicon.ico", "/icon_",
    "icon_facebook", "icon_instagram", "icon_google", "icon_youtube",
    "icon_pinterest", "icon_twitter", "icon_linkedin",
    "/banner", "/logo", "/header", "/footer", "/ad", "/promo",
    "/carousel", "/social", "/share",
    "/uhleft", "/uhright", "/whiteborder",  # AALT legal archive UI chrome
)

# Optional override from CLI: (patterns_tuple, clear_defaults)
_skip_patterns_config: tuple[tuple[str, ...], bool] | None = None


def set_skip_patterns(extra: list[str] | None = None, clear: bool = False) -> None:
    """Configure skip patterns. extra=patterns to add, clear=True to ignore defaults."""
    global _skip_patterns_config
    if extra is not None or clear:
        base = () if clear else SKIP_IMAGE_PATTERNS
        added = tuple(extra or [])
        _skip_patterns_config = (base + added, clear)
    else:
        _skip_patterns_config = None


def _effective_skip_patterns() -> tuple[str, ...]:
    """Return patterns to use for should_skip_image_url."""
    if _skip_patterns_config is not None:
        return _skip_patterns_config[0]
    return SKIP_IMAGE_PATTERNS


# URL substrings that indicate tracking/analytics pixels (skip to avoid 400s and noise)
TRACKING_URL_SUBSTRINGS = ("facebook.com/tr", "google-analytics.com", "googletagmanager.com", "doubleclick.net", "scorecardresearch.com")

# Data attributes for lazy-loaded or high-res images (order: prefer hires over lazy)
IMG_DATA_ATTRS = (
    "data-zoom-src", "data-full-url", "data-hires", "data-highres", "data-large",
    "data-src", "data-lazy-src", "data-original", "data-srcset", "data-full", "data-image", "data-url",
)
# Path segments that suggest an image URL (for extension-less a[href])
IMG_PATH_HINTS = ("/image", "/img", "/photo", "/media", "/thumb", "/icaimage", "/gallery", "/asset")

# link[rel="alternate"] type priority: JPEG/TIFF first
_ALTERNATE_TYPE_PRIORITY = ("image/jpeg", "image/jpg", "image/tiff", "image/tif")


def infer_expected_images(html_str: str) -> int | None:
    """
    Parse common patterns in HTML to infer expected image count.
    Returns the inferred count or None if no pattern matches.
    """
    if not html_str or not isinstance(html_str, str):
        return None
    # "Contains: N images" (e.g. Wellcome Collection)
    m = re.search(r"Contains:\s*(\d+)\s+images?", html_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "X/Y" pagination (e.g. "1/58" - use second number as total; skip date-like YYYY/MM, MM/DD)
    m = re.search(r"\b(\d+)/(\d+)\b", html_str)
    if m:
        first, second = int(m.group(1)), int(m.group(2))
        # Skip dates: YYYY/MM (first >= 1000) or MM/DD (first <= 12, second <= 31, first != 1)
        if first < 1000 and (first > 12 or second > 31 or first == 1):
            return second
    # "X of Y" (e.g. "Page 1 of 58")
    m = re.search(r"(\d+)\s+of\s+(\d+)\b", html_str, re.IGNORECASE)
    if m:
        return int(m.group(2))
    return None


def should_skip_image_url(url: str) -> bool:
    """True if URL looks like UI chrome (favicon, social icons) or tracking pixels."""
    path = urlparse(url).path.lower()
    if any(p in path for p in _effective_skip_patterns()):
        return True
    url_lower = url.lower()
    return any(t in url_lower for t in TRACKING_URL_SUBSTRINGS)


# Path extensions that indicate a file (not a directory); if absent, treat as directory for urljoin
_PATH_FILE_EXTENSIONS = (
    ".html", ".htm", ".xhtml", ".php", ".asp", ".aspx", ".jsp", ".cgi",
    ".pdf", ".xml", ".json", ".txt", ".csv", ".rss", ".atom",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
)


def _base_for_relative(base_url: str) -> str:
    """
    Normalize base_url so relative paths resolve correctly.
    When the URL looks like a directory (no file extension), ensure trailing slash.
    E.g. http://aalt.law.uh.edu/E1/CP40no1A -> .../CP40no1A/ so thumbnail_IMG_0595.JPG
    resolves to .../CP40no1A/thumbnail_IMG_0595.JPG instead of .../E1/thumbnail_IMG_0595.JPG.
    """
    if not base_url or base_url.endswith("/"):
        return base_url
    parsed = urlparse(base_url)
    path = parsed.path or "/"
    last = path.rstrip("/").split("/")[-1] if path else ""
    if not last:
        return base_url.rstrip("/") + "/"
    last_lower = last.lower()
    if any(last_lower.endswith(ext) for ext in _PATH_FILE_EXTENSIONS):
        return base_url
    return base_url.rstrip("/") + "/"


def _resolve_urls(base_url: str, seen: set[str], *candidates: str) -> list[str]:
    """Resolve candidate hrefs/srcs to absolute URLs and return new ones (deduped)."""
    base = _base_for_relative(base_url)
    out: list[str] = []
    for raw in candidates:
        if not raw or not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw or raw.startswith(("#", "mailto:", "javascript:", "data:")):
            continue
        u = urljoin(base, raw)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

THUMB_TO_FULL = [
    (r"/thumb(s|nails?)/", "/full/"),
    (r"thumbnail_", ""),  # AALT: thumbnail_IMG_0595.JPG -> IMG_0595.JPG
    (r"/small/", "/large/"),
    (r"/_s\.", "/_b."),
    (r"-thumb", ""),
    (r"_thumb", ""),
    (r"/thumb/", "/original/"),
    (r"thumbnail", "original"),
]


def find_pdf_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Collect PDF links from a[href], object[data], embed[src]. Single select() pass."""
    seen: set[str] = set()
    urls: list[str] = []

    def add_pdf(href: str) -> None:
        for u in _resolve_urls(base_url, seen, href):
            urls.append(u)

    for tag in soup.select("a[href], object[data], embed[src]"):
        href = tag.get("href") or tag.get("data") or tag.get("src")
        if not href:
            continue
        if tag.name == "a":
            if href.lower().endswith(".pdf"):
                add_pdf(href)
            elif (tag.get("type") or "").strip().lower() == "application/pdf":
                add_pdf(href)
        else:
            if href.lower().endswith(".pdf") or (tag.get("type") or "").strip().lower() == "application/pdf":
                add_pdf(href)

    return urls


def _parse_srcset(srcset: str, base_url: str) -> list[tuple[str, int]]:
    """Parse srcset attribute; return [(url, width)] with width 0 if descriptor missing."""
    base = _base_for_relative(base_url)
    entries: list[tuple[str, int]] = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        width = 0
        for b in bits[1:]:
            if b.endswith("w"):
                try:
                    width = int(b[:-1])
                except ValueError:
                    pass
                break
        abs_url = urljoin(base, url)
        entries.append((abs_url, width))
    return entries


def _pick_largest_srcset(entries: list[tuple[str, int]]) -> str | None:
    """Return URL with largest width; if tied, prefer JPEG/TIFF."""
    if not entries:
        return None
    best = max(entries, key=lambda x: (x[1], image_format_priority(x[0])))
    return best[0]


def _try_high_res_url(url: str) -> str:
    """Apply thumbnail->full URL heuristics."""
    result = url
    for pattern, repl in THUMB_TO_FULL:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def _add_image_from_attr(base_url: str, seen: set[str], urls: list[str], val: str) -> None:
    """Resolve one image URL (or pick from srcset) and append if new."""
    if not val:
        return
    if " " in val and ("srcset" in val or "," in val):
        entries = _parse_srcset(val, base_url)
        picked = _pick_largest_srcset(entries)
        if picked and picked not in seen:
            seen.add(picked)
            urls.append(picked)
    else:
        for u in _resolve_urls(base_url, seen, val):
            urls.append(u)


def find_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """
    Collect image URLs from img, source, video poster, a[href], object/embed.
    Single select() pass over the tree for efficiency.
    """
    seen: set[str] = set()
    urls: list[str] = []

    def add_url(val: str) -> None:
        _add_image_from_attr(base_url, seen, urls, val)

    for tag in soup.select("img, source, video, a, object, embed"):
        name = tag.name
        if name == "img":
            srcset = tag.get("srcset")
            if srcset:
                add_url(srcset)
                continue
            for attr in IMG_DATA_ATTRS:
                val = tag.get(attr)
                if val:
                    add_url(val)
                    break
            else:
                if tag.get("src"):
                    add_url(tag["src"])
        elif name == "source":
            if tag.get("srcset"):
                add_url(tag["srcset"])
            elif tag.get("src") and _looks_like_image(tag["src"]):
                add_url(tag["src"])
        elif name == "video" and tag.get("poster"):
            add_url(tag["poster"])
        elif name == "a":
            href = (tag.get("href") or "").strip()
            if href and _looks_like_image(href):
                add_url(href)
        elif name in ("object", "embed"):
            data = tag.get("data") or tag.get("src")
            if data and _looks_like_image(data):
                add_url(data)

    # link[rel="preload"][as="image"] (common in modern galleries)
    for link in soup.select('link[rel="preload"][as="image"][href]'):
        href = link.get("href")
        if href:
            add_url(href)

    # link[rel="alternate"][type^="image/"] (LOC, etc. - download options: TIFF, JPEG2000, etc.)
    # Prioritize JPEG/TIFF over JP2/others
    alternate_links = [
        (link.get("href"), (link.get("type") or "").strip().lower())
        for link in soup.select('link[rel="alternate"][href]')
    ]
    alternate_links.sort(
        key=lambda x: next((i for i, t in enumerate(_ALTERNATE_TYPE_PRIORITY) if x[1].startswith(t)), 99)
    )
    for href, type_attr in alternate_links:
        if type_attr.startswith("image/") and href and _looks_like_image(href):
            add_url(href)

    # style="background-image: url(...)" or background: url(...)
    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        for match in _STYLE_URL_RE.finditer(style):
            url = match.group(1).strip()
            if url and not url.startswith("data:") and _looks_like_image(url):
                add_url(url)

    return urls


def find_contentdm_full_res_urls(page_url: str, raw_html: str = "") -> list[str]:
    """
    For CONTENTdm (e.g. hdl.huntington.org): derive full-resolution IIIF image URLs.
    - If page_url is a CONTENTdm item (/digital/collection/{coll}/id/{id}), add IIIF full-size URL.
    - If raw_html contains IIIF Image API URLs under /digital/iiif/2/, rewrite them to /full/full/0/default.jpg.
    See: https://help.oclc.org/.../IIIF_API_reference
    """
    seen: set[str] = set()
    out: list[str] = []

    parsed = urlparse(page_url)
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"

    # Current page is a CONTENTdm item?
    m = _CONTENTDM_ITEM_RE.search(parsed.path or "")
    if m:
        coll, rec_id = m.group(1), m.group(2)
        full_url = f"{base}/digital/iiif/2/{coll}:{rec_id}/full/full/0/default.jpg"
        if full_url not in seen:
            seen.add(full_url)
            out.append(full_url)

    # Any IIIF image URLs in HTML (thumbnails) -> full-res
    if raw_html:
        for m in _IIIF_IMAGE_API_RE.finditer(raw_html):
            prefix, ext = m.group(1), m.group(2).lower()
            full_url = f"{prefix}/full/full/0/default.{ext}"
            if full_url not in seen:
                seen.add(full_url)
                out.append(full_url)

    return out


def find_nypl_manifest_urls(page_url: str) -> list[str]:
    """
    For NYPL Digital Collections (digitalcollections.nypl.org/items/{uuid}):
    manifest is at api-collections.nypl.org, not on the item domain.
    """
    if not _NYPL_ITEMS_RE.match(page_url):
        return []
    m = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", page_url, re.I)
    if not m:
        return []
    uuid = m.group(0)
    return [f"https://api-collections.nypl.org/manifests/{uuid}"]


def find_nypl_iiif_image_urls(raw_html: str) -> list[str]:
    """
    Extract NYPL IIIF 3 image URLs from HTML and rewrite to full size.
    IIIF 3 uses 'max' for size (not 'full'); format: .../full/max/0/default.jpg
    """
    urls = []
    seen = set()
    for m in _NYPL_IIIF3_RE.finditer(raw_html):
        prefix, ext = m.group(1), m.group(2)
        full_url = f"{prefix}/full/max/0/default.{ext}"
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)
    return urls


def find_hathitrust_imgsrv_urls(raw_html: str, page_url: str) -> list[str]:
    """
    Extract HathiTrust imgsrv image URLs from HTML and rewrite to full size (size=10000).
    """
    from strigil.hathitrust import hathitrust_image_url

    urls: list[str] = []
    seen: set[str] = set()

    def add_full(url: str) -> None:
        parsed = urlparse(url)
        if "hathitrust.org" not in parsed.netloc.lower():
            return
        query = (parsed.query or "").replace(";", "&")
        qs = parse_qs(query)
        if "id" not in qs:
            return
        vol_id = qs["id"][0]
        seq = int(qs.get("seq", ["1"])[0])
        new_url = hathitrust_image_url(vol_id, seq)
        if new_url not in seen:
            seen.add(new_url)
            urls.append(new_url)

    for m in _HATHITRUST_IMGSRV_RE.finditer(raw_html):
        add_full(m.group(1))

    m = _HATHITRUST_PT_RE.search(page_url)
    if m:
        vol_id = m.group(1)
        parsed = urlparse(page_url)
        query = (parsed.query or "").replace(";", "&")
        qs = parse_qs(query)
        for seq in qs.get("seq", ["1"]):
            full_url = hathitrust_image_url(vol_id, int(seq))
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)

    return urls


def find_iiif_manifest_urls(soup: BeautifulSoup, base_url: str, raw_html: str = "") -> list[str]:
    """
    Find IIIF manifest URLs from iframes (Universal Viewer, Mirador, etc.), links, and page text.
    Returns absolute manifest URLs, deduplicated.
    """
    seen: set[str] = set()
    urls: list[str] = []

    def add_manifest(u: str) -> None:
        u = u.strip()
        if not u or u in seen:
            return
        # Must be a manifest endpoint, not a viewer URL that contains manifest=
        if "/manifest.json" not in u.lower() or "uv.html" in u.lower() or "mirador" in u.lower():
            return
        abs_u = urljoin(str(base_url), str(u))
        if abs_u not in seen:
            seen.add(abs_u)
            urls.append(abs_u)

    # iframe src (e.g. viewer.library.wales/...uv.html#?manifest=https://.../manifest.json)
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        for m in _IIIF_MANIFEST_RE.finditer(src):
            add_manifest(m.group(1))
        for m in _IIIF_MANIFEST_URL_RE.finditer(src):
            add_manifest(m.group(0))

    # a[href] and embed/object data
    for tag in soup.select("a[href], embed[src], object[data]"):
        attr = tag.get("href") or tag.get("src") or tag.get("data") or ""
        for m in _IIIF_MANIFEST_RE.finditer(attr):
            add_manifest(m.group(1))
        for m in _IIIF_MANIFEST_URL_RE.finditer(attr):
            add_manifest(m.group(0))

    # data-manifest, data-iiif-manifest, etc.
    for tag in soup.find_all(attrs=lambda a: a and "manifest" in a.lower()):
        for key, val in (tag.attrs or {}).items():
            if "manifest" in key.lower() and val:
                add_manifest(val)

    # Scan raw HTML for embedded manifest URLs
    if raw_html:
        for m in _IIIF_MANIFEST_RE.finditer(raw_html):
            add_manifest(m.group(1))
        for m in _IIIF_MANIFEST_URL_RE.finditer(raw_html):
            add_manifest(m.group(0))

    return urls


def find_derived_iiif_manifest_urls(page_url: str) -> list[str]:
    """
    Derive IIIF manifest URLs from known page URL patterns (JS-heavy sites where
    manifest isn't in HTML). Strategies:
    - manifest= / iiif-content= in query or fragment (Universal Viewer, Mirador)
    - Digital Bodleian: .../objects/{uuid}/... -> iiif.bodleian.ox.ac.uk
    - Internet Archive: archive.org/details/{id} -> iiif.archive.org
    - Stanford PURL: purl.stanford.edu/{id} -> purl.stanford.edu/{id}/iiif/manifest
    """
    urls: list[str] = []
    parsed = urlparse(page_url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    combined = f"{host}{path}"

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u:
            return
        u = unquote(u)
        if not u.startswith(("http://", "https://")):
            return
        if u in urls:
            return
        if "manifest" in u.lower() or "/iiif/" in u.lower():
            urls.append(u)

    # 1. manifest= or iiif-content= in query string or fragment (Universal Viewer, Mirador)
    for part in (parsed.query or "", parsed.fragment or ""):
        if part.startswith("?"):
            part = part[1:]
        if not part:
            continue
        for key in ("manifest", "iiif-content", "iiif_content"):
            for v in parse_qs(part).get(key, []):
                add(v)

    # 2. Digital Bodleian
    m = _BODLEIAN_OBJECT_RE.search(combined)
    if m:
        add(f"https://iiif.bodleian.ox.ac.uk/iiif/manifest/{m.group(1)}.json")

    # 3. Internet Archive
    m = _ARCHIVE_ORG_DETAILS_RE.search(combined)
    if m:
        add(f"https://iiif.archive.org/iiif/{m.group(1)}/manifest.json")

    # 4. Stanford PURL
    m = _STANFORD_PURL_RE.search(combined)
    if m:
        add(f"https://purl.stanford.edu/{m.group(1)}/iiif/manifest")

    return urls


def iiif_text_value(label: Any) -> str | None:
    """Normalize IIIF 2/3 label (string, language map, or list) to a single display string."""
    if label is None:
        return None
    if isinstance(label, str):
        s = label.strip()
        return s or None
    if isinstance(label, list):
        for item in label:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, list) and v and isinstance(v[0], str):
                        s = v[0].strip()
                        if s:
                            return s
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        return None
    if isinstance(label, dict):
        for v in label.values():
            if isinstance(v, list) and v and isinstance(v[0], str):
                s = v[0].strip()
                if s:
                    return s
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    return str(label).strip() or None


def iiif_value_to_json_serializable(val: Any, *, _depth: int = 0) -> Any:
    """
    Convert IIIF Presentation values (language maps, nested objects, HTML strings)
    to JSON-serializable structures. Drops noisy @context blobs.
    """
    if _depth > 40:
        return str(val)[:8000] if val is not None else None
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, list):
        return [iiif_value_to_json_serializable(x, _depth=_depth + 1) for x in val]
    if isinstance(val, dict):
        out: dict[str, Any] = {}
        for k, v in val.items():
            if not isinstance(k, str):
                continue
            if k == "@context":
                continue
            nk = "id" if k == "@id" else k
            if nk.startswith("@") and nk not in ("@value", "@language", "@none"):
                continue
            out[nk] = iiif_value_to_json_serializable(v, _depth=_depth + 1)
        return out
    return str(val)


def extract_iiif_descriptive_manifest(
    manifest_data: dict,
    *,
    manifest_url: str | None = None,
    source_page_url: str | None = None,
) -> dict[str, Any]:
    """
    Full catalog / description block from a IIIF 2.x or 3.x Presentation manifest.
    Includes label, summary, metadata, rights, and other common descriptive fields.
    """
    block: dict[str, Any] = {}
    if manifest_url:
        block["manifest_url"] = manifest_url
    if manifest_url or source_page_url:
        block["sources"] = {
            "kind": "iiif_presentation",
            "manifest_url": manifest_url,
            "page_url": source_page_url,
        }
    mid = manifest_data.get("id") or manifest_data.get("@id")
    if mid:
        block["id"] = str(mid)
    typ = manifest_data.get("type")
    if typ:
        block["type"] = typ
    if manifest_data.get("label") is not None:
        block["label"] = iiif_value_to_json_serializable(manifest_data.get("label"))
    if manifest_data.get("summary") is not None:
        block["summary"] = iiif_value_to_json_serializable(manifest_data.get("summary"))
    if manifest_data.get("metadata") is not None:
        block["metadata"] = iiif_value_to_json_serializable(manifest_data.get("metadata"))
    for key in (
        "requiredStatement",
        "rights",
        "viewingDirection",
        "behavior",
        "partOf",
        "homepage",
        "seeAlso",
        "provider",
        "thumbnail",
        "navDate",
    ):
        if manifest_data.get(key) is not None:
            block[key] = iiif_value_to_json_serializable(manifest_data[key])
    return block


def extract_html_description_snippets(soup: BeautifulSoup, page_url: str) -> dict[str, Any] | None:
    """
    Pull common HTML-level catalog text (meta description, Open Graph, document title,
    first JSON-LD block) when no IIIF manifest is available or as a supplement.
    """
    out: dict[str, Any] = {
        "sources": {
            "kind": "html_document",
            "page_url": page_url,
        }
    }
    meta = soup.find("meta", attrs={"name": lambda x: x and str(x).lower() == "description"})
    if meta and meta.get("content"):
        c = str(meta["content"]).strip()
        if c:
            out["meta_description"] = c
    for prop, key in (("og:title", "og_title"), ("og:description", "og_description")):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            c = str(tag["content"]).strip()
            if c:
                out[key] = c
    tw = soup.find("meta", attrs={"name": lambda x: x and str(x).lower() == "twitter:description"})
    if tw and tw.get("content"):
        c = str(tw["content"]).strip()
        if c:
            out["twitter_description"] = c
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        t = title_tag.get_text(strip=True)
        if t:
            out["document_title"] = t
    for script in soup.find_all("script", attrs={"type": lambda x: x and "ld+json" in str(x).lower()}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw or len(raw) > 100_000:
            continue
        try:
            parsed = json.loads(raw)
            out["json_ld"] = iiif_value_to_json_serializable(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            out["json_ld_raw"] = raw[:50_000]
        break
    if len(out) <= 1:
        return None
    return out


def _iiif_manifest_object_meta(manifest_data: dict) -> tuple[str | None, Any]:
    """Work-level label and @id/id for the manifest."""
    return (
        iiif_text_value(manifest_data.get("label")),
        manifest_data.get("@id") or manifest_data.get("id"),
    )


def _to_full_res_iiif(url: str) -> str:
    """Rewrite IIIF URL to full resolution (full/max/0/default.jpg)."""
    if "/full/max/" in url or "/full/full/" in url:
        return url
    if "/full/" in url and ("iiif" in url.lower() or "iiif" in url):
        base = url.split("/full/")[0]
        tail = "/0/default.jpg"
        if "/0/default." in url:
            tail = url[url.find("/0/default.") :]
        return f"{base}/full/max{tail}"
    return url


def _image_from_iiif_resource(res: dict, service_id: str | None = None) -> str | None:
    svc = res.get("service")
    sid = None
    if isinstance(svc, dict):
        sid = svc.get("@id") or svc.get("id")
    elif isinstance(svc, list) and svc:
        first = svc[0]
        sid = (first.get("@id") or first.get("id")) if isinstance(first, dict) else None
    sid = sid or service_id
    if sid:
        return f"{str(sid).rstrip('/')}/full/max/0/default.jpg"
    rid = res.get("@id") or res.get("id")
    if isinstance(rid, str) and ("iiif" in rid.lower() or rid.endswith((".jpg", ".png", ".jpeg", ".webp"))):
        return _to_full_res_iiif(rid)
    return None


def _best_url_from_canvas_rendering(rendering: list) -> str | None:
    """Pick full-resolution URL from rendering options (e.g. NYPL)."""
    if not isinstance(rendering, list):
        return None
    full_max = None
    for r in rendering:
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or r.get("@id")
        if not isinstance(rid, str):
            continue
        if "/full/max/" in rid:
            return rid
        if "/full/full/" in rid:
            full_max = rid
    return full_max


def _canvas_main_image_url(canvas: dict) -> str | None:
    """Single best image URL for a IIIF Presentation canvas (full-res IIIF Image API where possible)."""
    u = _best_url_from_canvas_rendering(canvas.get("rendering"))
    if u:
        return u
    pages = canvas.get("items") or []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for ann in page.get("items") or []:
            if not isinstance(ann, dict):
                continue
            body = ann.get("body")
            if isinstance(body, dict):
                url = _image_from_iiif_resource(body)
                if url:
                    return url
    images = canvas.get("images") or []
    for img in images:
        res = img.get("resource") if isinstance(img, dict) else None
        if not res:
            continue
        url = _image_from_iiif_resource(res)
        if url:
            return url
    return None


def parse_iiif_manifest_pages(
    manifest_data: dict,
    *,
    manifest_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Parse IIIF 2.x / 3.x manifest: ordered pages with image URLs and canvas labels.
    Each dict includes: url, page_index (1-based), label, canvas_id, manifest_url,
    object_label, manifest_id.
    """
    object_label, manifest_id = _iiif_manifest_object_meta(manifest_data)
    pages: list[dict[str, Any]] = []
    page_index = 0

    items_or_seqs = manifest_data.get("sequences") or manifest_data.get("items") or []
    for thing in items_or_seqs:
        if not isinstance(thing, dict):
            continue
        if thing.get("type") == "Canvas":
            canvas_list = [thing]
        else:
            canvas_list = [
                c for c in (thing.get("canvases") or thing.get("items") or []) if isinstance(c, dict)
            ]
        for canvas in canvas_list:
            url = _canvas_main_image_url(canvas)
            if not url:
                continue
            page_index += 1
            cid = canvas.get("@id") or canvas.get("id")
            cid_str = str(cid) if cid is not None else None
            pages.append(
                {
                    "url": url,
                    "page_index": page_index,
                    "label": iiif_text_value(canvas.get("label")),
                    "canvas_id": cid_str,
                    "manifest_url": manifest_url,
                    "object_label": object_label,
                    "manifest_id": str(manifest_id) if manifest_id is not None else None,
                }
            )
    return pages


def parse_iiif_manifest(manifest_data: dict) -> list[str]:
    """
    Parse IIIF 2.0 or 3.0 manifest JSON; return list of full-size image URLs.
    Supports sequences/canvases (2.0), items (3.0), and NYPL-style rendering.
    """
    return [p["url"] for p in parse_iiif_manifest_pages(manifest_data)]


def _looks_like_image(url_or_path: str) -> bool:
    """True if URL/path appears to reference an image (extension or path hints)."""
    path = urlparse(url_or_path).path.lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tif", ".tiff", ".jp2")):
        return True
    return any(hint in path for hint in IMG_PATH_HINTS)


_STYLE_URL_RE = re.compile(r"url\s*\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)", re.IGNORECASE)


def get_best_image_url(
    url: str,
    head_content_type: str | None,
    *,
    try_high_res: bool = True,
) -> str:
    """
    Given an image URL and optional Content-Type from HEAD, return the best URL to use.
    If try_high_res and URL looks like a thumbnail, try heuristics and return that.
    Caller should HEAD the result to verify it exists; fallback to original if not.
    """
    if not try_high_res:
        return url
    high_res = _try_high_res_url(url)
    return high_res if high_res != url else url


def extract_text(soup: BeautifulSoup, raw_html: str | bytes = "") -> str:
    """
    Extract main text from HTML. Prefer readability-lxml; fallback to BeautifulSoup
    content selectors (main, article, [role="main"], .content, then body).
    Returns normalized UTF-8 text.
    """
    try:
        from readability import Document

        doc = Document(raw_html if raw_html else str(soup))
        summary = doc.summary()
        if summary:
            s = BeautifulSoup(summary, "lxml")
            text = s.get_text(separator="\n", strip=True)
            return _normalize_text(text)
    except ImportError:
        pass
    # Fallback: strip noise, then find main content via select()
    soup_copy = BeautifulSoup(str(soup), "lxml")
    for tag in soup_copy.select("script, style, nav, header, footer, aside, noscript, iframe"):
        tag.decompose()
    # Prefer semantic/ARIA main content, then body
    main = (
        soup_copy.select_one("main, article, [role='main'], .content, .article, .post-content, .entry-content")
        or soup_copy.find("body")
        or soup_copy
    )
    text = main.get_text(separator="\n", strip=True) if main else ""
    return _normalize_text(text)


def extract_text_html(soup: BeautifulSoup, raw_html: str | bytes = "") -> str:
    """
    Extract main document content as HTML (lists, tables, div structure preserved).
    Prefer readability-lxml summary HTML; else a cleaned clone of the main region
    (#tabContent, main, article, .content, …) with script/style/nav stripped.
    """
    raw = raw_html if raw_html else str(soup)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        from readability import Document

        doc = Document(raw)
        summary = doc.summary()
        if summary and summary.strip():
            frag = BeautifulSoup(summary, "lxml")
            root = frag.body if frag.body else frag
            for tag in root.select("script, style, noscript"):
                tag.decompose()
            inner = root.decode_contents().strip() if hasattr(root, "decode_contents") else str(root).strip()
            if inner:
                return inner
    except ImportError:
        pass
    except Exception:
        pass

    soup_copy = BeautifulSoup(str(soup), "lxml")
    for tag in soup_copy.select("script, style, nav, header, footer, aside, noscript, iframe"):
        tag.decompose()
    main = soup_copy.select_one(
        "#tabContent, .tab-content.panel, .tab-content, "
        "main, article, [role='main'], .content, .article, .post-content, .entry-content, "
        ".manuscript-content, #content, .container .row .col-md-9, .ms-description"
    ) or soup_copy.find("body") or soup_copy
    if not main:
        return ""
    if hasattr(main, "decode_contents"):
        return main.decode_contents().strip()
    return str(main).strip()


def _normalize_text(s: str) -> str:
    """Normalize whitespace and ensure valid text."""
    lines = (line.strip() for line in s.splitlines())
    return "\n".join(line for line in lines if line)


def find_page_links(soup: BeautifulSoup, base_url: str, same_domain: str | None) -> list[str]:
    """Find links to HTML pages for crawling. Uses select('a[href]'); filters by scheme and domain."""
    seen: set[str] = set()
    urls: list[str] = []
    base_domain = urlparse(base_url).netloc if same_domain else None
    asset_extensions = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip")

    for a in soup.select("a[href]"):
        for abs_url in _resolve_urls(base_url, seen, a.get("href", "")):
            parsed = urlparse(abs_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.path.lower().endswith(asset_extensions):
                continue
            if same_domain and parsed.netloc != base_domain:
                continue
            urls.append(abs_url)

    return urls
