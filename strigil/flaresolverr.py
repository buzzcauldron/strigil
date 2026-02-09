"""
FlareSolverr integration: fetch HTML via FlareSolverr to bypass Cloudflare/DDoS-GUARD.

FlareSolverr is a proxy server that solves Cloudflare challenges in a headless browser
and returns the cleared HTML and cookies. See: https://github.com/FlareSolverr/FlareSolverr
"""

import os
from typing import Any

import httpx

DEFAULT_FLARESOLVERR_URL = "http://localhost:8191"
DEFAULT_TIMEOUT_MS = 60_000


def get_flaresolverr_url() -> str | None:
    """Return FlareSolverr base URL from env FLARESOLVERR_URL, or None if not set."""
    url = os.environ.get("FLARESOLVERR_URL", "").strip()
    return url or None


def fetch_html(
    url: str,
    base_url: str = DEFAULT_FLARESOLVERR_URL,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_seconds: float | None = None,
) -> tuple[bytes, str]:
    """
    Fetch a URL via FlareSolverr; returns (html_bytes, charset).

    :param url: The page URL to fetch (e.g. https://example.com/page).
    :param base_url: FlareSolverr API base (e.g. http://localhost:8191).
    :param timeout_ms: Max time for FlareSolverr to solve the challenge (ms).
    :param wait_seconds: Optional wait after solving (for dynamic content).
    :returns: (raw HTML bytes, charset) — charset is always "utf-8" from FlareSolverr.
    :raises RuntimeError: If FlareSolverr returns an error or request fails.
    """
    api_url = base_url.rstrip("/") + "/v1"
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    if wait_seconds is not None and wait_seconds > 0:
        payload["waitInSeconds"] = wait_seconds

    with httpx.Client(timeout=timeout_ms / 1000.0 + 30) as client:
        resp = client.post(api_url, json=payload)
        resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status != "ok":
        msg = data.get("message", "Unknown FlareSolverr error")
        raise RuntimeError(f"FlareSolverr error: {msg}")

    solution = data.get("solution") or {}
    html = solution.get("response")
    if html is None:
        raise RuntimeError("FlareSolverr returned no response body")
    if isinstance(html, bytes):
        return html, "utf-8"
    return html.encode("utf-8", errors="replace"), "utf-8"
