"""ECCO (Eighteenth Century Collections Online) adapter with Internet Archive bypass.

Strategy: ECCO is paywalled (Gale). For Gale ECCO URLs we first try to locate the
same work on Internet Archive via ESTC / Gale document id / title search, then
fall back to PDF download links embedded in the page HTML (requires an
authenticated Gale session via :mod:`strigil.auth`).
"""

from __future__ import annotations

import re
from typing import Callable

from strigil.adapters.ia_lookup import probe_internet_archive, title_from_html
from strigil.extractors import _ECCO_DOMAIN_RE

# ESTC numbers common on ECCO record pages.
_ESTC_RE = re.compile(r"\bESTC\b\s*[:#]?\s*([SRN][0-9]{3,8})", re.IGNORECASE)
# Gale document / page ids in URL or HTML.
_GALE_DOC_ID_RE = re.compile(
    r'(?:docId|pageId|documentId)=["\']?([A-Za-z0-9_-]{4,})',
    re.IGNORECASE,
)
_GALE_ECCO_PATH_ID_RE = re.compile(
    r'/apps/ECCO(?:/|(?:\?|$))[^"\'<>]*?(?:[?&]id=|/)([A-Za-z0-9_-]{4,})',
    re.IGNORECASE,
)

# Gale "Download PDF" / full-text delivery links (patterns vary by campus proxy).
_GALE_PDF_HREF_RE = re.compile(
    r'href=["\'](?P<href>[^"\']*(?:downloadPDF|DownloadPDF|/download/|'
    r'deliverable|fulltext|\.pdf\?)[^"\']*)["\']',
    re.IGNORECASE,
)


def _identifiers_from(url: str, html: str) -> dict[str, str]:
    pool = f"{url or ''}\n{html or ''}"
    out: dict[str, str] = {}
    m = _ESTC_RE.search(pool)
    if m:
        out["estc"] = m.group(1).strip()
    for pattern, label in (
        (_GALE_DOC_ID_RE, "doc_id"),
        (_GALE_ECCO_PATH_ID_RE, "ecco_id"),
    ):
        m = pattern.search(pool)
        if m:
            out[label] = m.group(1).strip()
    return out


def _ia_search_queries(idents: dict[str, str], *, title: str | None = None) -> list[str]:
    queries: list[str] = []
    if title:
        queries.append(f'title:"{title}"')
        queries.append(f"{title} eighteenth century")
    if "estc" in idents:
        queries.append(f"{idents['estc']}")
        queries.append(f'"{idents["estc"]}" eighteenth century')
    if "doc_id" in idents:
        queries.append(f"{idents['doc_id']} gale ecco")
    if "ecco_id" in idents:
        queries.append(f"{idents['ecco_id']} eighteenth century collections")
    return queries


def _try_gale_pdf(url: str, html: str) -> list[str]:
    if not html:
        return []
    hrefs = [m.group("href") for m in _GALE_PDF_HREF_RE.finditer(html)]
    out: list[str] = []
    for h in hrefs:
        if h.startswith("//"):
            h = "https:" + h
        elif h.startswith("/"):
            h = "https://link.gale.com" + h
        out.append(h)
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


class EccoAdapter:
    """ECCO → IA-first fallback chain; Gale auth is the last resort."""

    def matches(self, url: str) -> bool:
        return bool(_ECCO_DOMAIN_RE.search(url or ""))

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
        return _try_gale_pdf(url or "", html or "")
