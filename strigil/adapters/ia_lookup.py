"""Internet Archive metadata search helpers for paywalled archive adapters."""

from __future__ import annotations

import json
import re
from typing import Callable
from urllib.parse import quote_plus

_HTML_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)


def clean_record_title(
    raw: str | None,
    *,
    portal_suffixes: tuple[str, ...] = ("ProQuest", "Gale"),
) -> str | None:
    """Strip vendor suffixes from a record page title for IA title search."""
    if not raw:
        return None
    t = raw.strip()
    for suffix in portal_suffixes:
        t = re.sub(rf"\s*[-–]\s*{re.escape(suffix)}\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+by\s+[A-Z].*$", "", t)
    return t.strip() or None


def title_from_html(html: str) -> str | None:
    m = _HTML_TITLE_RE.search(html or "")
    return clean_record_title(m.group(1)) if m else None


def advancedsearch_urls(queries: list[str]) -> list[str]:
    """Build IA advancedsearch.php probe URLs for the given query strings."""
    return [
        "https://archive.org/advancedsearch.php?"
        f"q={quote_plus(q)}&fl[]=identifier&fl[]=title&rows=3&output=json"
        for q in queries
        if q.strip()
    ]


def probe_internet_archive(
    fetch: Callable[[str], bytes] | None,
    queries: list[str],
) -> list[str]:
    """Return IA PDF download URLs when metadata search finds a match."""
    if not fetch or not queries:
        return []
    for probe_url in advancedsearch_urls(queries):
        try:
            raw = fetch(probe_url)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        docs = (data.get("response") or {}).get("docs") or []
        if not docs:
            continue
        ident = docs[0].get("identifier")
        if not ident:
            continue
        return [f"https://archive.org/download/{ident}/{ident}.pdf"]
    return []
