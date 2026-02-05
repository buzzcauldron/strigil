"""HTTP fetching with retries, streaming, and politeness (User-Agent, timeouts)."""

import random
import time
from pathlib import Path

import httpx


def _polite_sleep(delay: float) -> None:
    """Sleep with ±15% jitter plus small random offset to avoid fixed-interval bot patterns."""
    jittered = delay * random.uniform(0.85, 1.15) if delay > 0 else 0.0
    # Keep extra offset small for low delays so aggressive scrapes stay fast
    extra_cap = 0.02 if delay < 0.5 else 0.05
    extra_ms = random.uniform(0, extra_cap)
    total = jittered + extra_ms
    if total > 0:
        time.sleep(total)

# Browser-like UA to reduce 403 from sites that block scrapers
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0  # auto timeout cap: per-attempt timeout scales up to this
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # multiplicative factor for delay and timeout scaling

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class Fetcher:
    """HTTP fetcher with connection pooling. Reuse for multiple requests."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
        use_browser: bool = False,
    ) -> None:
        self._timeout = timeout
        self._headers = {**DEFAULT_HEADERS, **(headers or {})}
        self._use_browser = use_browser
        self._client: httpx.Client | None = None
        # Playwright: one context per Fetcher so cookies from first page load apply to assets
        self._playwright = None
        self._browser = None
        self._browser_context = None
        # Page URL from last fetch_html; sent as Referer on asset requests to reduce 403s
        self._page_url: str | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=self._timeout,
                headers=self._headers,
            )
        return self._client

    def _get_browser_context(self):
        """Lazy-init Playwright browser and context; shared so cookies apply to all requests."""
        if self._browser_context is not None:
            return self._browser_context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Browser fetch (--js) requires: pip install basic-scraper[js] && playwright install"
            ) from e
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._browser_context = self._browser.new_context()
        return self._browser_context

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()
        if self._browser_context is not None:
            try:
                self._browser_context.close()
            except Exception:
                pass
            self._browser_context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def fetch_html(self, url: str, *, delay: float = 0) -> tuple[bytes, str]:
        """Fetch HTML; returns (raw_bytes, charset). Uses browser (Playwright) when use_browser=True."""
        if self._use_browser:
            _polite_sleep(delay)
            ctx = self._get_browser_context()
            self._page_url = url
            last_exc: BaseException | None = None
            for attempt in range(MAX_RETRIES):
                attempt_timeout_ms = min(
                    self._timeout * (RETRY_BACKOFF ** attempt) * 1000,
                    MAX_TIMEOUT * 1000,
                )
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=int(attempt_timeout_ms))
                    # Brief wait for lazy-loaded images and JS-injected content
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        time.sleep(1.5)
                    html = page.content()
                    return html.encode("utf-8"), "utf-8"
                except Exception as e:
                    last_exc = e
                    if attempt < MAX_RETRIES - 1:
                        _polite_sleep(RETRY_BACKOFF ** attempt)
                        continue
                    raise
                finally:
                    page.close()
        _polite_sleep(delay)
        last_exc = None
        for attempt in range(MAX_RETRIES):
            attempt_timeout = min(
                self._timeout * (RETRY_BACKOFF ** attempt),
                MAX_TIMEOUT,
            )
            try:
                client = self._get_client()
                resp = client.get(url, timeout=attempt_timeout)
                resp.raise_for_status()
                return resp.content, resp.charset_encoding or "utf-8"
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                r = getattr(e, "response", None)
                if r is not None:
                    code = getattr(r, "status_code", None)
                    retryable = code in (403, 429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1
                    if retryable:
                        wait = RETRY_BACKOFF ** attempt
                        if r is not None and code in (403, 429):
                            ra = (r.headers or {}).get("retry-after", "").strip()
                            if ra and ra.isdigit():
                                wait = max(wait, float(ra))
                        _polite_sleep(wait)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def fetch_binary(self, url: str, dest_path: Path, *, timeout: float = 60.0, delay: float = 0) -> bool:
        """Stream download to dest_path. Returns True on success."""
        _polite_sleep(delay)
        if self._use_browser:
            ctx = self._get_browser_context()
            headers = {"Referer": self._page_url} if self._page_url else {}
            for attempt in range(MAX_RETRIES):
                attempt_timeout_ms = min(
                    timeout * (RETRY_BACKOFF ** attempt) * 1000,
                    MAX_TIMEOUT * 1000,
                )
                resp = ctx.request.get(url, timeout=attempt_timeout_ms, headers=headers or None)
                if resp.ok:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(resp.body())
                    return True
                if resp.status == 403 and attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF ** attempt
                    ra = (resp.headers or {}).get("retry-after", "").strip()
                    if ra and ra.isdigit():
                        wait = max(wait, float(ra))
                    _polite_sleep(wait)
                    continue
                raise RuntimeError(
                    f"Client error '{resp.status} {resp.status_text}' for url '{url}'"
                )
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            attempt_timeout = min(timeout * (RETRY_BACKOFF ** attempt), MAX_TIMEOUT)
            try:
                client = self._get_client()
                with client.stream("GET", url, timeout=attempt_timeout) as resp:
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
                    retryable = code in (403, 429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1
                    if retryable:
                        wait = RETRY_BACKOFF ** attempt
                        if r is not None and code in (403, 429):
                            ra = (r.headers or {}).get("retry-after", "").strip()
                            if ra and ra.isdigit():
                                wait = max(wait, float(ra))
                        _polite_sleep(wait)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def head_metadata(self, url: str, *, timeout: float = 10.0, delay: float = 0) -> tuple[str | None, int | None]:
        """HEAD request; returns (content_type, content_length). content_length is None if header missing."""
        _polite_sleep(delay)
        head_timeout = min(timeout, MAX_TIMEOUT)
        if self._use_browser:
            try:
                ctx = self._get_browser_context()
                headers = {"Referer": self._page_url} if self._page_url else {}
                resp = ctx.request.head(url, timeout=head_timeout * 1000, headers=headers or None)
                if not resp.ok:
                    return None, None
                ct = resp.headers.get("content-type", "")
                content_type = ct.split(";")[0].strip().lower() if ct else None
                cl = resp.headers.get("content-length")
                content_length = int(cl) if cl is not None and cl.isdigit() else None
                return content_type, content_length
            except Exception:
                return None, None
        try:
            client = self._get_client()
            resp = client.head(url, timeout=head_timeout)
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
