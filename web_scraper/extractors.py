"""Extract PDF links, main text, and image URLs from HTML."""

import json
import re
from urllib.parse import urljoin, urlparse

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
# IIIF Image API URL with size/region (e.g. /full/pct:15/) -> we want full size (full/full; max not supported on all servers)
_IIIF_IMAGE_API_RE = re.compile(
    r"(https?://[^/]+/digital/iiif/2/[^/]+)/full/[^/]+/\d+/[^/]+\.(jpg|png|webp)",
    re.IGNORECASE,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"}

# URL path patterns to skip (UI chrome: favicons, social icons, etc.)
SKIP_IMAGE_PATTERNS = ("/favicon.ico", "/icon_", "icon_facebook", "icon_instagram", "icon_google", "icon_youtube", "icon_pinterest", "icon_twitter", "icon_linkedin")

# Data attributes for lazy-loaded or high-res images (order: prefer hires over lazy)
IMG_DATA_ATTRS = (
    "data-zoom-src", "data-full-url", "data-hires", "data-highres", "data-large",
    "data-src", "data-lazy-src", "data-original", "data-srcset", "data-full", "data-image", "data-url",
)
# Path segments that suggest an image URL (for extension-less a[href])
IMG_PATH_HINTS = ("/image", "/img", "/photo", "/media", "/thumb", "/icaimage", "/gallery", "/asset")


def should_skip_image_url(url: str) -> bool:
    """True if URL looks like UI chrome (favicon, social icons) rather than content."""
    path = urlparse(url).path.lower()
    return any(p in path for p in SKIP_IMAGE_PATTERNS)


def _resolve_urls(base_url: str, seen: set[str], *candidates: str) -> list[str]:
    """Resolve candidate hrefs/srcs to absolute URLs and return new ones (deduped)."""
    out: list[str] = []
    for raw in candidates:
        if not raw or not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw or raw.startswith(("#", "mailto:", "javascript:", "data:")):
            continue
        u = urljoin(base_url, raw)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

THUMB_TO_FULL = [
    (r"/thumb(s|nails?)/", "/full/"),
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
        abs_url = urljoin(base_url, url)
        entries.append((abs_url, width))
    return entries


def _pick_largest_srcset(entries: list[tuple[str, int]]) -> str | None:
    """Return URL with largest width; if none have width, return first."""
    if not entries:
        return None
    best = max(entries, key=lambda x: x[1])
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


def parse_iiif_manifest(manifest_data: dict) -> list[str]:
    """
    Parse IIIF 2.0 or 3.0 manifest JSON; return list of full-size image URLs.
    Supports sequences/canvases (2.0) and items (3.0).
    """
    image_urls: list[str] = []
    seen: set[str] = set()

    def add_url(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            image_urls.append(u)

    def image_from_resource(res: dict, service_id: str | None = None) -> str | None:
        svc = res.get("service")
        sid = None
        if isinstance(svc, dict):
            sid = svc.get("@id") or svc.get("id")
        sid = sid or service_id
        # Prefer IIIF Image API URL (full/full/0/default.jpg) - more reliable than direct resource URL
        if sid:
            return f"{str(sid).rstrip('/')}/full/full/0/default.jpg"
        rid = res.get("@id") or res.get("id")
        if isinstance(rid, str) and ("iiif" in rid.lower() or rid.endswith((".jpg", ".png", ".jpeg", ".webp"))):
            return rid
        return None

    def walk_canvas(canvas: dict) -> None:
        images = canvas.get("images") or canvas.get("items") or []
        for img in images:
            res = img.get("resource") if isinstance(img, dict) else None
            if not res:
                continue
            svc = (res.get("service") or {})
            svc_id = (svc.get("@id") or svc.get("id")) if isinstance(svc, dict) else None
            url = image_from_resource(res, svc_id)
            if url:
                add_url(url)

    sequences = manifest_data.get("sequences") or manifest_data.get("items") or []
    for seq in sequences:
        canvases = seq.get("canvases") or seq.get("items") or []
        for canvas in canvases:
            walk_canvas(canvas)

    return image_urls


def _looks_like_image(url_or_path: str) -> bool:
    """True if URL/path appears to reference an image (extension or path hints)."""
    path = urlparse(url_or_path).path.lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico")):
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
