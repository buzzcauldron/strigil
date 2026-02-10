"""HTTP fetching with retries, streaming, and politeness (User-Agent, timeouts)."""

import random
import sys
import time
from pathlib import Path

import httpx

# Phrases that indicate a rate-limit page (200 body) so we throttle and retry
RATE_LIMIT_PHRASES = (b"rate limit", b"too many requests", b"throttl", b"slow down", b"try again")

def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After header; return seconds to wait, or None."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        diff = dt.timestamp() - time.time()
        return max(1.0, diff) if diff > 0 else None
    except Exception:
        return None


def _body_indicates_rate_limit(content: bytes) -> bool:
    """True if response body looks like a rate-limit or throttle message."""
    if not content or len(content) > 50_000:
        return False
    lower = content.lower()
    return any(phrase in lower for phrase in RATE_LIMIT_PHRASES)


def _iiif_alternate_url(url: str) -> str | None:
    """Return an alternate IIIF Image API URL to try on 501, or None."""
    if "/full/full/" in url:
        return url.replace("/full/full/", "/full/max/", 1)
    if "/full/max/" in url:
        return url.replace("/full/max/", "/full/full/", 1)
    return None


def _is_retryable_5xx(code: int | None) -> bool:
    """True if status is a transient server error we should retry."""
    return code in (500, 502, 503, 504)


def _wait_for_retry(code: int | None, attempt: int, retry_after_header: str | None) -> float:
    """Return seconds to wait before retry. Longer for 5xx."""
    from_header = _parse_retry_after(retry_after_header)
    if from_header is not None:
        return from_header
    if _is_retryable_5xx(code):
        return BASE_WAIT_5XX * (RETRY_BACKOFF ** attempt)
    return RETRY_BACKOFF ** attempt


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
MAX_RETRIES_5XX = 6  # more retries for 502/503/504 (server often recovers after a short wait)
RETRY_BACKOFF = 2.0  # multiplicative factor for delay and timeout scaling
BASE_WAIT_5XX = 5.0  # base wait in seconds before retrying on 502/503/504

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
        flaresolverr_url: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._headers = {**DEFAULT_HEADERS, **(headers or {})}
        self._use_browser = use_browser
        self._flaresolverr_url = flaresolverr_url
        self._client: httpx.Client | None = None
        # Playwright: one context per Fetcher so cookies from first page load apply to assets
        self._playwright = None
        self._browser = None
        self._browser_context = None
        # Page URL from last fetch_html; sent as Referer on asset requests to reduce 403s
        self._page_url: str | None = None
        # After 429 or rate-limit body, throttle subsequent requests by this many seconds
        self._rate_limit_delay: float = 0.0

    def _sleep(self, delay: float) -> None:
        """Sleep at least delay; add extra if we're in rate-limit backoff."""
        effective = max(delay, self._rate_limit_delay)
        _polite_sleep(effective)

    def spawn(self) -> "Fetcher":
        """Return a new Fetcher with the same config (for use in another thread)."""
        return Fetcher(
            timeout=self._timeout,
            headers=self._headers,
            use_browser=self._use_browser,
        )

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
                "Browser fetch (--js) requires: pip install strigil && playwright install"
            ) from e
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as e:
            exc_str = str(e).lower()
            if "executable doesn't exist" in exc_str or "executable does not exist" in exc_str:
                import subprocess
                import sys
                print("Installing Playwright Chromium (one-time)...", file=sys.stderr)
                subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    check=True,
                    timeout=300,
                )
                self._browser = self._playwright.chromium.launch(headless=True)
            else:
                raise
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
        """Fetch HTML; returns (raw_bytes, charset). Uses FlareSolverr, browser (Playwright), or httpx."""
        if self._flaresolverr_url:
            self._sleep(delay)
            from strigil.flaresolverr import fetch_html as flaresolverr_fetch
            self._page_url = url
            timeout_ms = min(int(self._timeout * 1000), int(MAX_TIMEOUT * 1000))
            return flaresolverr_fetch(url, self._flaresolverr_url, timeout_ms=timeout_ms)
        if self._use_browser:
            self._sleep(delay)
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
                    # Wait for lazy-loaded content; longer for known JS-heavy IIIF sites (NYPL, etc.)
                    networkidle_timeout = 4000
                    if "digitalcollections.nypl.org" in url or "universalviewer.io" in url:
                        networkidle_timeout = 15000
                    try:
                        page.wait_for_load_state("networkidle", timeout=networkidle_timeout)
                    except Exception:
                        time.sleep(2 if networkidle_timeout > 5000 else 1.5)
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
        self._sleep(delay)
        last_exc = None
        for attempt in range(MAX_RETRIES_5XX):
            attempt_timeout = min(
                self._timeout * (RETRY_BACKOFF ** attempt),
                MAX_TIMEOUT,
            )
            try:
                client = self._get_client()
                resp = client.get(url, timeout=attempt_timeout)
                resp.raise_for_status()
                # Some sites return 200 with a rate-limit message in the body
                if _body_indicates_rate_limit(resp.content):
                    wait = _parse_retry_after((resp.headers or {}).get("retry-after")) or 60.0
                    self._rate_limit_delay = max(self._rate_limit_delay, wait)
                    if attempt < MAX_RETRIES_5XX - 1:
                        print(f"  Rate limit detected; waiting {wait:.0f}s then retrying...", file=sys.stderr)
                        _polite_sleep(wait)
                        continue
                # Decay throttle after success
                self._rate_limit_delay = max(0.0, self._rate_limit_delay * 0.9)
                return resp.content, resp.charset_encoding or "utf-8"
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                r = getattr(e, "response", None)
                if r is not None:
                    code = getattr(r, "status_code", None)
                    max_attempts = MAX_RETRIES_5XX if _is_retryable_5xx(code) else MAX_RETRIES
                    retryable = code in (403, 429, 500, 502, 503, 504) and attempt < max_attempts - 1
                    if retryable:
                        wait = _wait_for_retry(code, attempt, (r.headers or {}).get("retry-after"))
                        if code == 429:
                            wait = max(wait, 30.0)
                        self._rate_limit_delay = max(self._rate_limit_delay, wait)
                        if code == 429:
                            print(f"  Rate limit (429); waiting {wait:.0f}s then retrying...", file=sys.stderr)
                        elif code == 502:
                            print(f"  502 Bad Gateway; waiting {wait:.0f}s then retrying...", file=sys.stderr)
                        _polite_sleep(wait)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def fetch_bytes(self, url: str, *, delay: float = 0) -> bytes:
        """
        Fetch raw bytes (e.g. for IIIF manifests).
        For .json URLs in browser mode, uses in-page fetch() to bypass
        Incapsula/bot protection (e.g. NYPL Digital Collections).
        """
        self._sleep(delay)
        if self._use_browser and ("manifest.json" in url or url.rstrip("/").endswith(".json")):
            ctx = self._get_browser_context()
            timeout_ms = min(int(self._timeout * 1000), int(MAX_TIMEOUT * 1000))
            # In-page fetch has same-origin cookies; request API may be blocked by Incapsula
            page = ctx.new_page()
            try:
                page.goto(self._page_url or url, wait_until="domcontentloaded", timeout=timeout_ms)
                result = page.evaluate(
                    """async ([u, tout]) => {
                        const r = await fetch(u, {credentials: 'same-origin'});
                        if (!r.ok) throw new Error('fetch failed: ' + r.status);
                        return Array.from(new Uint8Array(await r.arrayBuffer()));
                    }""",
                    [url, timeout_ms],
                )
                page.close()
                if result:
                    return bytes(result)
            except Exception:
                try:
                    page.close()
                except Exception:
                    pass
                raise
        # Non-browser or non-JSON: use simple GET
        client = self._get_client()
        resp = client.get(url, timeout=min(self._timeout, MAX_TIMEOUT))
        resp.raise_for_status()
        return resp.content

    def fetch_binary(self, url: str, dest_path: Path, *, timeout: float = 60.0, delay: float = 0) -> bool:
        """Stream download to dest_path. Returns True on success."""
        self._sleep(delay)
        if self._use_browser:
            ctx = self._get_browser_context()
            headers = {"Referer": self._page_url} if self._page_url else {}
            for attempt in range(MAX_RETRIES_5XX):
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
                    wait = _wait_for_retry(403, attempt, (resp.headers or {}).get("retry-after"))
                    _polite_sleep(wait)
                    continue
                # 502/503/504: retry more times with longer waits (server often recovers)
                if _is_retryable_5xx(resp.status) and attempt < MAX_RETRIES_5XX - 1:
                    wait = _wait_for_retry(resp.status, attempt, (resp.headers or {}).get("retry-after"))
                    if resp.status == 502:
                        print(f"  502 Bad Gateway; waiting {wait:.0f}s then retrying...", file=sys.stderr)
                    _polite_sleep(wait)
                    continue
                # IIIF 501 Not Implemented: try alternate size (full/full <-> full/max)
                if resp.status == 501:
                    alt = _iiif_alternate_url(url)
                    if alt:
                        resp2 = ctx.request.get(alt, timeout=attempt_timeout_ms, headers=headers or None)
                        if resp2.ok:
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            dest_path.write_bytes(resp2.body())
                            return True
                raise RuntimeError(
                    f"Client error '{resp.status} {resp.status_text}' for url '{url}'"
                )
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES_5XX):
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
                    # IIIF 501 Not Implemented: try alternate size (full/full <-> full/max)
                    if code == 501:
                        alt = _iiif_alternate_url(url)
                        if alt:
                            try:
                                with client.stream("GET", alt, timeout=attempt_timeout) as resp2:
                                    resp2.raise_for_status()
                                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                                    with open(dest_path, "wb") as f:
                                        for chunk in resp2.iter_bytes(chunk_size=65536):
                                            f.write(chunk)
                                    return True
                            except (httpx.HTTPStatusError, httpx.RequestError):
                                pass
                    max_attempts = MAX_RETRIES_5XX if _is_retryable_5xx(code) else MAX_RETRIES
                    retryable = code in (403, 429, 500, 502, 503, 504) and attempt < max_attempts - 1
                    if retryable:
                        wait = _wait_for_retry(code, attempt, (r.headers or {}).get("retry-after"))
                        if code == 429:
                            wait = max(wait, 30.0)
                        self._rate_limit_delay = max(self._rate_limit_delay, wait)
                        if code == 429:
                            print(f"  Rate limit (429); waiting {wait:.0f}s then retrying...", file=sys.stderr)
                        elif code == 502:
                            print(f"  502 Bad Gateway; waiting {wait:.0f}s then retrying...", file=sys.stderr)
                        _polite_sleep(wait)
                        continue
                raise
        raise last_exc  # type: ignore[misc]

    def head_metadata(self, url: str, *, timeout: float = 10.0, delay: float = 0) -> tuple[str | None, int | None]:
        """HEAD request; returns (content_type, content_length). content_length is None if header missing."""
        self._sleep(delay)
        head_timeout = min(timeout, MAX_TIMEOUT)
        if self._use_browser:
            try:
                ctx = self._get_browser_context()
                headers = {"Referer": self._page_url} if self._page_url else {}
                resp = ctx.request.head(url, timeout=head_timeout * 1000, headers=headers or None)
                if not resp.ok:
                    if resp.status == 501:
                        alt = _iiif_alternate_url(url)
                        if alt:
                            resp2 = ctx.request.head(alt, timeout=head_timeout * 1000, headers=headers or None)
                            if resp2.ok:
                                ct = (resp2.headers or {}).get("content-type", "")
                                content_type = ct.split(";")[0].strip().lower() if ct else None
                                cl = (resp2.headers or {}).get("content-length")
                                content_length = int(cl) if cl is not None and str(cl).isdigit() else None
                                return content_type, content_length
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 501:
                alt = _iiif_alternate_url(url)
                if alt:
                    try:
                        resp2 = client.head(alt, timeout=head_timeout)
                        resp2.raise_for_status()
                        ct = resp2.headers.get("content-type", "")
                        content_type = ct.split(";")[0].strip().lower() if ct else None
                        cl = resp2.headers.get("content-length")
                        content_length = int(cl) if cl is not None and cl.isdigit() else None
                        return content_type, content_length
                    except Exception:
                        pass
            return None, None
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
