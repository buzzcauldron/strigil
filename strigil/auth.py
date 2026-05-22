"""Cookie / session loading for adapters that need authenticated fetches.

EEBO (ProQuest) and ECCO (Gale) downloads only work if the request carries a
session cookie from a logged-in browser. This module pulls those cookies
straight out of the local Chrome / Safari / Firefox profile via
``browser_cookie3`` so the user doesn't have to copy them by hand.

Optional dependency: install with ``pip install browser-cookie3`` (already a
transitive of nothing — left as a soft import so non-EEBO users don't pay).
"""

from __future__ import annotations


def load_browser_cookies(
    domain_substring: str = "proquest.com",
    browser: str | None = None,
) -> dict[str, str]:
    """Return a ``{name: value}`` dict of cookies whose host contains ``domain_substring``.

    :param domain_substring: Substring to match against each cookie's domain.
        Default ``"proquest.com"`` covers EEBO sessions; use ``"gale.com"`` for
        ECCO; pass ``"archive.org"`` for IA-authenticated work, etc.
    :param browser: Optional override — one of ``"chrome"``, ``"safari"``,
        ``"firefox"``, ``"edge"``, ``"brave"``. When ``None``, browser_cookie3
        scans all supported browsers in order.
    """
    try:
        import browser_cookie3  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "browser_cookie3 is required for session-cookie loading; "
            "install with `pip install browser-cookie3`."
        ) from e

    jar_map = {
        "chrome": browser_cookie3.chrome,
        "safari": browser_cookie3.safari,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "brave": browser_cookie3.brave,
    }
    if browser:
        loaders = [jar_map[browser.lower()]]
    else:
        loaders = [browser_cookie3.load]  # union of all browsers

    out: dict[str, str] = {}
    for loader in loaders:
        try:
            jar = loader()
        except Exception:
            continue
        for c in jar:
            if domain_substring in (c.domain or ""):
                # Later entries win on collision (mirrors typical session refresh order).
                out[c.name] = c.value
    return out
