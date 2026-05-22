"""EEBO (Early English Books Online) adapter with parallel Internet Archive bypass.

Strategy: EEBO is paywalled (ProQuest). For any EEBO URL the adapter first
attempts to find the same work on Internet Archive (open access, no auth) by
extracting an identifier (STC / Wing / ESTC / VID) from the URL or page HTML
and querying IA's metadata API. If a match is found we return the IA artifact
URL — ``scan``/downstream pipelines can consume it without authentication. If
no IA hit is found, we fall back to extracting the ProQuest "Document as PDF"
URL from the page HTML; that path requires a session cookie supplied by the
caller (see :mod:`strigil.auth`).
"""

from __future__ import annotations

import re
from typing import Callable

from strigil.adapters.ia_lookup import probe_internet_archive, title_from_html
from strigil.extractors import _EEBO_DOMAIN_RE


# Identifier patterns useful for IA cross-lookup. EEBO records expose multiple
# identifiers in URL/HTML; we try the most discriminating first.
_VID_RE = re.compile(r"vid[=:]?\s*([0-9]{3,})", re.IGNORECASE)
# Pollard & Redgrave STC: "STC 12345" / "STC 12345a" / "STC (1) 12345"
_STC_RE = re.compile(
    r"\bSTC\s*\(?\s*(?:I|1st)?\s*\)?\s*[:#]?\s*([0-9]{1,6}[A-Za-z]?(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
# Wing — explicit "Wing X1234" form *or* the convention "STC (II) X1234" /
# "STC (2nd) X1234" which ProQuest pages often use to encode Wing numbers.
_WING_RE = re.compile(
    r"(?:\bWing\b\s*[:#]?\s*|\bSTC\s*\(?\s*(?:II|2nd)\s*\)?\s*[:#]?\s*)"
    r"([A-Z]\s*[0-9]{1,5}[A-Za-z]?)",
    re.IGNORECASE,
)
_ESTC_RE = re.compile(r"\bESTC\b\s*[:#]?\s*([SRN][0-9]{3,8})", re.IGNORECASE)

# ProQuest "Download document as PDF" / "Full text PDF" URL patterns observed
# in EEBO record HTML. ProQuest tweaks these between rollouts — keep the regex
# permissive and revisit when a fetch returns no candidates against a real
# page.
_PROQUEST_PDF_HREF_RE = re.compile(
    r'href=["\'](?P<href>[^"\']*(?:fulltextPDF|/fulltext/|deliveryWS|deliverable_unit_pdf)[^"\']*)["\']',
    re.IGNORECASE,
)


def _identifiers_from(url: str, html: str) -> dict[str, str]:
    """Extract whatever bibliographic identifiers we can find in URL or HTML."""
    pool = f"{url or ''}\n{html or ''}"
    out: dict[str, str] = {}
    for label, pattern in (("vid", _VID_RE), ("stc", _STC_RE), ("wing", _WING_RE), ("estc", _ESTC_RE)):
        m = pattern.search(pool)
        if m:
            out[label] = m.group(1).strip()
    return out


def _ia_search_queries(idents: dict[str, str], *, title: str | None = None) -> list[str]:
    """Return IA advancedsearch query strings in priority order."""
    queries: list[str] = []
    if title:
        queries.append(f'title:"{title}"')
    if "wing" in idents:
        wing = idents["wing"].replace(" ", "")
        queries.append(f"{wing} early english")
    if "stc" in idents:
        queries.append(f'"STC {idents["stc"]}"')
    if "estc" in idents:
        queries.append(f"{idents['estc']}")
    return queries


def _try_proquest_pdf(url: str, html: str) -> list[str]:
    """Extract ProQuest 'Document as PDF' URLs from page HTML.

    Returns up to one URL — the highest-priority one matched.
    The fetcher upstream must already be authenticated against ProQuest
    (e.g. via ``strigil.auth.load_browser_cookies``) for the URL to download.
    """
    if not html:
        return []
    hrefs = [m.group("href") for m in _PROQUEST_PDF_HREF_RE.finditer(html)]
    out: list[str] = []
    for h in hrefs:
        if h.startswith("//"):
            h = "https:" + h
        elif h.startswith("/"):
            h = "https://www.proquest.com" + h
        out.append(h)
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


class EeboAdapter:
    """EEBO → IA-first fallback chain; ProQuest auth is the last resort."""

    def matches(self, url: str) -> bool:
        return bool(_EEBO_DOMAIN_RE.search(url or ""))

    def extract_image_urls(
        self,
        url: str,
        html: str,
        fetch: Callable[[str], bytes] | None,
    ) -> list[str]:
        idents = _identifiers_from(url or "", html or "")
        title = title_from_html(html or "")
        ia_hits = probe_internet_archive(
            fetch,
            _ia_search_queries(idents, title=title),
        )
        if ia_hits:
            return ia_hits
        return _try_proquest_pdf(url or "", html or "")
