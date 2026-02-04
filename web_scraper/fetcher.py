"""HTTP fetching with retries, streaming, and politeness (User-Agent, timeouts)."""

import time
from pathlib import Path

import httpx

USER_AGENT = "WebScraper/1.0 (+https://github.com/; high-quality PDF/text/image scraper)"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # multiplicative factor


class Fetcher:
    """HTTP fetcher with connection pooling. Reuse for multiple requests."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def fetch_html(self, url: str, *, delay: float = 0) -> tuple[bytes, str]:
        """Fetch HTML; returns (raw_bytes, charset)."""
        if delay > 0:
            time.sleep(delay)
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                client = self._get_client()
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content, resp.charset_encoding or "utf-8"
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                r = getattr(e, "response", None)
                if r is not None:
                    code = getattr(r, "status_code", None)
                    if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF ** attempt)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def fetch_binary(self, url: str, dest_path: Path, *, timeout: float = 60.0, delay: float = 0) -> bool:
        """Stream download to dest_path. Returns True on success."""
        if delay > 0:
            time.sleep(delay)
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                client = self._get_client()
                with client.stream("GET", url, timeout=timeout) as resp:
                    resp.raise_for_status()
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                return True
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                r = getattr(e, "response", None)
                if r is not None:
                    code = getattr(r, "status_code", None)
                    if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF ** attempt)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def head_metadata(self, url: str, *, timeout: float = 10.0, delay: float = 0) -> tuple[str | None, int | None]:
        """HEAD request; returns (content_type, content_length). content_length is None if header missing."""
        if delay > 0:
            time.sleep(delay)
        try:
            client = self._get_client()
            resp = client.head(url, timeout=timeout)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            content_type = ct.split(";")[0].strip().lower() if ct else None
            cl = resp.headers.get("content-length")
            content_length = int(cl) if cl is not None and cl.isdigit() else None
            return content_type, content_length
        except Exception:
            return None, None

    def head_content_type(self, url: str, *, timeout: float = 10.0, delay: float = 0) -> str | None:
        """HEAD request to get Content-Type. Returns None on failure."""
        ct, _ = self.head_metadata(url, timeout=timeout, delay=delay)
        return ct


def fetch_html(url: str, *, timeout: float = DEFAULT_TIMEOUT, delay: float = 0) -> tuple[bytes, str]:
    """Standalone fetch (creates temporary client). Prefer Fetcher for multiple requests."""
    with Fetcher(timeout=timeout) as f:
        return f.fetch_html(url, delay=delay)


def fetch_binary(url: str, dest_path: Path, *, timeout: float = 60.0, delay: float = 0) -> bool:
    """Standalone fetch. Prefer Fetcher for multiple requests."""
    with Fetcher(timeout=timeout) as f:
        return f.fetch_binary(url, dest_path, timeout=timeout, delay=delay)


def head_content_type(url: str, *, timeout: float = 10.0, delay: float = 0) -> str | None:
    """Standalone HEAD. Prefer Fetcher for multiple requests."""
    with Fetcher(timeout=timeout) as f:
        return f.head_content_type(url, timeout=timeout, delay=delay)
