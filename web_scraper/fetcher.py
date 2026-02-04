"""HTTP fetching with retries, streaming, and politeness (User-Agent, timeouts)."""

import time
from pathlib import Path

import httpx

USER_AGENT = "WebScraper/1.0 (+https://github.com/; high-quality PDF/text/image scraper)"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # multiplicative factor


def _get_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )


def fetch_html(url: str, *, timeout: float = DEFAULT_TIMEOUT, delay: float = 0) -> tuple[bytes, str]:
    """
    Fetch HTML at URL, respecting Content-Type and charset.
    Returns (raw_bytes, encoding_str). Uses retries with backoff for 5xx/429.
    """
    if delay > 0:
        time.sleep(delay)

    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with _get_client(timeout=timeout) as client:
                resp = client.get(url)
                resp.raise_for_status()
                content = resp.content
                charset = resp.charset_encoding or "utf-8"
                return content, charset
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            last_exc = e
            status = getattr(e, "response", None)
            if status is not None:
                code = getattr(status, "status_code", None)
                if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF ** attempt
                    time.sleep(wait)
                    continue
            raise
    raise last_exc  # type: ignore[misc]


def fetch_binary(
    url: str,
    dest_path: Path,
    *,
    timeout: float = 60.0,
    delay: float = 0,
) -> bool:
    """
    Download URL to dest_path. Streams for large files. Returns True if written.
    Uses retries with backoff for 5xx/429.
    """
    if delay > 0:
        time.sleep(delay)

    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with _get_client(timeout=timeout) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                    return True
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            last_exc = e
            response = getattr(e, "response", None)
            if response is not None:
                code = getattr(response, "status_code", None)
                if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF ** attempt
                    time.sleep(wait)
                    continue
            raise
    raise last_exc  # type: ignore[misc]


def head_content_type(url: str, *, timeout: float = 10.0, delay: float = 0) -> str | None:
    """HEAD request to get Content-Type. Returns None on failure."""
    if delay > 0:
        time.sleep(delay)
    try:
        with _get_client(timeout=timeout) as client:
            resp = client.head(url)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            return ct.split(";")[0].strip().lower() if ct else None
    except Exception:
        return None
