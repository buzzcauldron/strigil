"""Extract PDF links, main text, and image URLs from HTML."""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"}
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
    """Collect all PDF links: a[href] ending with .pdf or type=application/pdf."""
    seen: set[str] = set()
    urls: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        # Direct .pdf link
        if href.lower().endswith(".pdf"):
            seen.add(abs_url)
            urls.append(abs_url)
            continue
        # Link with type="application/pdf"
        if (a.get("type") or "").strip().lower() == "application/pdf":
            seen.add(abs_url)
            urls.append(abs_url)

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


def find_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """
    Collect image URLs from img[src], img[srcset], source[srcset], data-src, etc.
    Prefer largest from srcset; apply high-res heuristics when only thumbnail URL.
    """
    seen: set[str] = set()
    urls: list[str] = []

    def add_url(u: str) -> None:
        u = urljoin(base_url, u)
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    for img in soup.find_all("img"):
        # srcset: pick largest
        srcset = img.get("srcset")
        if srcset:
            entries = _parse_srcset(srcset, base_url)
            picked = _pick_largest_srcset(entries)
            if picked:
                add_url(picked)
                continue
        # data-src, data-lazy-src, etc.
        for attr in ("data-src", "data-lazy-src", "data-original", "data-srcset"):
            val = img.get(attr)
            if val:
                if " " in val:
                    entries = _parse_srcset(val, base_url)
                    picked = _pick_largest_srcset(entries)
                    if picked:
                        add_url(picked)
                else:
                    add_url(val)
                break
        else:
            # src
            src = img.get("src")
            if src:
                abs_url = urljoin(base_url, src)
                add_url(abs_url)

    for source in soup.find_all("source", srcset=True):
        srcset = source.get("srcset", "")
        if srcset:
            entries = _parse_srcset(srcset, base_url)
            picked = _pick_largest_srcset(entries)
            if picked:
                add_url(picked)

    return urls


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
    Extract main text from HTML. Prefer readability-lxml; fallback to tag heuristics.
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
    # Fallback: main/article content, strip script/style
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    main = soup.find(["main", "article"]) or soup.find("body") or soup
    text = main.get_text(separator="\n", strip=True) if main else ""
    return _normalize_text(text)


def _normalize_text(s: str) -> str:
    """Normalize whitespace and ensure valid text."""
    lines = (line.strip() for line in s.splitlines())
    return "\n".join(line for line in lines if line)


def find_page_links(soup: BeautifulSoup, base_url: str, same_domain: str | None) -> list[str]:
    """Find links to HTML pages for crawling. If same_domain, only return same-domain links."""
    seen: set[str] = set()
    urls: list[str] = []

    base_domain = urlparse(base_url).netloc if same_domain else None

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip")):
            continue
        if same_domain and parsed.netloc != base_domain:
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        urls.append(abs_url)

    return urls
