"""Recover data from JS-rendered pages WITHOUT running a browser.

Rationale
---------
A site using JavaScript does not necessarily require a JS engine to scrape. Two
very common patterns leave the data reachable over plain HTTP:

  1. The payload is already in the HTML, inside a ``<script>`` tag, and JS only
     moves it into the DOM. Next.js (``__NEXT_DATA__``), Nuxt (``__NUXT__``),
     Redux/Vue SSR (``window.__INITIAL_STATE__``), Apollo
     (``__APOLLO_STATE__``), and schema.org JSON-LD all do this.
  2. The page is an empty shell that fetches its content from a JSON API. That
     endpoint is usually a plain GET and often discoverable from the shell's own
     markup or scripts.

Trying both before escalating to Playwright is strictly cheaper: no browser
launch (~1-3 s and ~300 MB RSS each), no ``playwright install``, no headless
fingerprint for bot detection to catch, and it works in environments where a
browser cannot run at all.

This module is deliberately pure and side-effect free -- parsing and URL
derivation only, no fetching -- so it is testable without network access.
Fetching stays in ``strigil.fetcher``.
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

__all__ = [
    "EmbeddedData",
    "extract_embedded_json",
    "extract_json_ld",
    "looks_js_shelled",
    "discover_api_endpoints",
    "iter_json_strings",
]

# ── script-tag payload patterns ─────────────────────────────────────────────
# Framework state blobs, in rough order of how self-contained the payload is.
_SCRIPT_ASSIGNMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # <script id="__NEXT_DATA__" type="application/json">{...}</script>
    ("__NEXT_DATA__", re.compile(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        re.I | re.S)),
    # window.__INITIAL_STATE__ = {...};
    ("__INITIAL_STATE__", re.compile(
        r'__INITIAL_STATE__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
    ("__INITIAL_DATA__", re.compile(
        r'__INITIAL_DATA__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
    ("__PRELOADED_STATE__", re.compile(
        r'__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
    ("__APOLLO_STATE__", re.compile(
        r'__APOLLO_STATE__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
    # window.__NUXT__ = {...} — may be a function call in newer Nuxt; the
    # object form is the one we can parse without evaluating JS.
    ("__NUXT__", re.compile(
        r'__NUXT__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
    ("__DATA__", re.compile(
        r'\bwindow\.__DATA__\s*=\s*(\{.*?\})\s*[;<]', re.S)),
)

_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', re.I | re.S)
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)

_SCRIPT_BLOCK_RE = re.compile(r'<script\b.*?</script>', re.I | re.S)
_STYLE_BLOCK_RE = re.compile(r'<style\b.*?</style>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')

# Candidate API endpoints referenced from the shell. Deliberately conservative:
# an absolute or root-relative path that looks like a data endpoint.
_API_URL_RE = re.compile(
    r'["\'](?P<url>(?:https?://[^"\'\s]+|/[^"\'\s]*)'
    r'(?:/api/|/graphql|/v\d+/|\.json)(?:[^"\'\s]*)?)["\']',
    re.I)

# IIIF is common in manuscript libraries and is always a plain GET.
# The `/` requirement matters: without it this also matches the bare JSON KEY in
# {"manifest":"/iiif/x/manifest.json"}, and urljoin() then turns that stray word
# into a plausible-looking but wrong sibling URL.
_IIIF_HINT_RE = re.compile(
    r'["\'](?P<url>(?:https?://|/)[^"\'\s]*manifest(?:\.json)?[^"\'\s]*)["\']', re.I)


class EmbeddedData:
    """Structured payloads recovered from a page's own markup.

    ``sources`` maps a label (``__NEXT_DATA__``, ``json-ld``, ...) to the parsed
    object. ``api_urls`` are absolute candidate data endpoints. ``js_shelled``
    reports whether the page rendered essentially no visible text, which is the
    signal that content is arriving via JS.
    """

    __slots__ = ("sources", "api_urls", "js_shelled", "visible_chars")

    def __init__(
        self,
        sources: dict[str, Any],
        api_urls: list[str],
        js_shelled: bool,
        visible_chars: int,
    ) -> None:
        self.sources = sources
        self.api_urls = api_urls
        self.js_shelled = js_shelled
        self.visible_chars = visible_chars

    def __bool__(self) -> bool:
        return bool(self.sources) or bool(self.api_urls)

    def __repr__(self) -> str:
        return (f"EmbeddedData(sources={sorted(self.sources)}, "
                f"api_urls={len(self.api_urls)}, js_shelled={self.js_shelled}, "
                f"visible_chars={self.visible_chars})")


def _as_text(html: str | bytes) -> str:
    if isinstance(html, bytes):
        return html.decode("utf-8", errors="replace")
    return html


def _balanced_json(text: str, start: int) -> str | None:
    """Return the complete JSON object/array beginning at *start*.

    The regexes above use a non-greedy ``\\{.*?\\}`` which stops at the first
    closing brace and therefore truncates any nested object. Rather than attempt
    a regex that can match balanced delimiters (it cannot), re-scan from the
    opening brace counting depth, while respecting string literals and escapes
    so that braces inside values do not affect the count.
    """
    if start >= len(text) or text[start] not in "{[":
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _loads(raw: str) -> Any | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    # Payloads inside HTML attributes or text nodes are often entity-encoded.
    try:
        return json.loads(unescape(raw))
    except (ValueError, TypeError):
        return None


def extract_json_ld(html: str | bytes) -> list[Any]:
    """Return every parsed ``application/ld+json`` block."""
    text = _as_text(html)
    out: list[Any] = []
    for m in _JSON_LD_RE.finditer(text):
        parsed = _loads(m.group(1))
        if parsed is None:
            continue
        # A single block may hold a list of entities.
        if isinstance(parsed, list):
            out.extend(parsed)
        else:
            out.append(parsed)
    return out


def visible_text_length(html: str | bytes) -> int:
    """Approximate count of human-visible characters, scripts/styles removed."""
    text = _as_text(html)
    text = _SCRIPT_BLOCK_RE.sub(" ", text)
    text = _STYLE_BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return len(" ".join(unescape(text).split()))


def looks_js_shelled(html: str | bytes, *, min_visible: int = 400) -> bool:
    """True when the page has scripts but almost no visible text.

    Such a response is a client-side shell: fetching it again the same way will
    never yield content. Either the payload is in one of the script blocks, or
    it comes from an API call the shell makes after load.
    """
    text = _as_text(html)
    if not _SCRIPT_BLOCK_RE.search(text):
        return False
    return visible_text_length(text) < min_visible


def discover_api_endpoints(html: str | bytes, base_url: str = "") -> list[str]:
    """Candidate JSON/API endpoints referenced by the page, absolutised.

    Ordered with IIIF manifests first (manuscript libraries almost always expose
    one, and it is the highest-value target), then other API-shaped URLs. Purely
    syntactic -- nothing is fetched or validated here.
    """
    text = _as_text(html)
    seen: set[str] = set()
    manifests: list[str] = []
    others: list[str] = []

    def _add(raw: str, bucket: list[str]) -> None:
        url = raw.strip()
        if not url or url.startswith(("data:", "javascript:", "#")):
            return
        if base_url:
            url = urljoin(base_url, url)
        elif url.startswith("/"):
            return  # cannot absolutise a root-relative URL without a base
        if url not in seen:
            seen.add(url)
            bucket.append(url)

    for m in _IIIF_HINT_RE.finditer(text):
        _add(m.group("url"), manifests)
    for m in _API_URL_RE.finditer(text):
        _add(m.group("url"), others)
    return manifests + others


def iter_json_strings(obj: Any, *, min_length: int = 40) -> list[str]:
    """Every string of at least *min_length* chars in a nested JSON structure.

    Useful for pulling transcription/description text out of a state blob whose
    schema is unknown, without having to map the whole shape first.
    """
    found: list[str] = []
    stack: list[Any] = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            if len(cur) >= min_length:
                found.append(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return found


def extract_embedded_json(html: str | bytes, base_url: str = "") -> EmbeddedData:
    """Recover script-tag payloads and candidate API endpoints from *html*.

    Returns an :class:`EmbeddedData`; falsy when nothing usable was found, in
    which case a real browser (``--js``) is the remaining option.
    """
    text = _as_text(html)
    sources: dict[str, Any] = {}

    for label, pattern in _SCRIPT_ASSIGNMENT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1)
        # Re-scan from the real opening brace so nested objects are not cut off
        # by the non-greedy pattern.
        brace = text.find(raw[0], m.start(1))
        balanced = _balanced_json(text, brace) if brace != -1 else None
        parsed = _loads(balanced if balanced else raw)
        if parsed is not None:
            sources[label] = parsed

    ld = extract_json_ld(text)
    if ld:
        sources["json-ld"] = ld

    generic: list[Any] = []
    for m in _JSON_SCRIPT_RE.finditer(text):
        # Skip the Next.js block already captured under its own label.
        if "__NEXT_DATA__" in text[max(0, m.start() - 200):m.start()]:
            continue
        parsed = _loads(m.group(1))
        if parsed is not None:
            generic.append(parsed)
    if generic:
        sources.setdefault("application/json", generic)

    return EmbeddedData(
        sources=sources,
        api_urls=discover_api_endpoints(text, base_url),
        js_shelled=looks_js_shelled(text),
        visible_chars=visible_text_length(text),
    )
