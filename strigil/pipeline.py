"""Scraping pipeline: map pages, scrape assets, crawl. Used by CLI and programmatic callers."""

import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Callable, ContextManager
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from strigil.discovery import collect_image_urls
from strigil.extractors import (
    find_page_links,
    find_pdf_urls,
    extract_text,
    get_best_image_url,
)
from strigil.fetcher import DEFAULT_TIMEOUT, Fetcher, MAX_TIMEOUT
from strigil.hardware import SAFE_ASSET_WORKERS, SAFE_HEAD_WORKERS
from strigil.robots import can_fetch
from strigil.storage import (
    load_manifest,
    manifest_path,
    path_exists_for_resource,
    path_for_image,
    path_for_image_canonical,
    path_for_pdf,
    path_for_pdf_canonical,
    path_for_text,
    path_for_text_canonical,
    sanitize_domain,
    save_manifest,
    write_text,
)


VALID_TYPES = frozenset({"pdf", "text", "images"})

ITERATION_DELAY_FACTOR = 1.2
ITERATION_TIMEOUT_FACTOR = 1.5

CRAWL_TIP = "  Tip: --workers 12 or --aggressiveness aggressive for faster crawl."

# IIIF full-res images (full/full region) are typically 1–5MB; use sequential to avoid timeouts.
LARGE_IIIF_MIN_COUNT = 10  # If this many+ IIIF full-res images, throttle to 1 worker


def _is_large_iiif_image(url: str) -> bool:
    """True if URL is IIIF Image API full-resolution (typically multi-MB)."""
    if not url or "/iiif/image/" not in url.lower():
        return False
    path = (urlparse(url).path or "").lower()
    return "/full/" in path  # full region = full resolution


def _effective_asset_workers(
    work_items: list[tuple[str, str, str | None]],
    requested: int,
    use_browser: bool,
) -> int:
    """Reduce parallelism when work is mostly large IIIF images (avoids timeouts)."""
    if use_browser or requested <= 1:
        return 1 if use_browser else requested
    image_items = [(u, b, ct) for u, b, ct in work_items if ct != "application/pdf"]
    large_count = sum(1 for _, best_url, _ in image_items if _is_large_iiif_image(best_url))
    if large_count >= LARGE_IIIF_MIN_COUNT and large_count >= len(image_items) // 2:
        return 1
    return min(requested, SAFE_ASSET_WORKERS, len(work_items) or 1)


def _effective_asset_workers_for_tasks(
    tasks: list[tuple[str, Path, str, str]],
    requested: int,
) -> int:
    """Like _effective_asset_workers but for (fetch_url, dest, ct, map_key) task format."""
    if requested <= 1:
        return requested
    image_tasks = [t for t in tasks if t[2] != "application/pdf"]
    large_count = sum(1 for fetch_url, _, _, _ in image_tasks if _is_large_iiif_image(fetch_url))
    if large_count >= LARGE_IIIF_MIN_COUNT and large_count >= len(image_tasks) // 2:
        return 1
    return min(requested, SAFE_ASSET_WORKERS, len(tasks) or 1)


def _should_skip_existing_by_size(
    fetcher: Fetcher, fetch_url: str, canon_path: Path, *, delay: float = 0
) -> bool:
    """True if file at canon_path exists and its size matches remote Content-Length."""
    if not canon_path.exists():
        return False
    try:
        _, content_length = fetcher.head_metadata(fetch_url, delay=delay)
        if content_length is not None and content_length == canon_path.stat().st_size:
            return True
    except Exception:
        pass
    return False


@dataclass
class MapResult:
    """Result of mapping a page: URLs to scrape, no downloads yet."""
    page_links: list[str] = field(default_factory=list)
    pdf_urls: list[str] = field(default_factory=list)
    image_items: list[tuple[str, str, str]] = field(default_factory=list)  # (url, best_url, content_type)
    text: tuple[str, str] | None = None  # (page_url, extracted_text) or None


def is_403(e: BaseException) -> bool:
    """True if the exception represents a 403 Forbidden."""
    if hasattr(e, "response") and e.response is not None:
        return getattr(e.response, "status_code", None) == 403
    return "403" in str(e)


def parse_size(s: str) -> int:
    """Parse size string to bytes: 100, 100k, 1m (case-insensitive)."""
    s = s.strip().lower()
    if not s:
        raise ValueError("empty size")
    if s.endswith("k"):
        return int(s[:-1]) * 1024
    if s.endswith("m"):
        return int(s[:-1]) * 1024 * 1024
    return int(s)


def head_one_image(
    img_url: str, fetcher: Fetcher, delay: float, *, use_shared: bool = True
) -> tuple[str, str, str | None, int | None] | None:
    """HEAD one image; return (url, best_url, content_type, content_length) or None."""
    f = fetcher if use_shared else Fetcher(timeout=10, use_browser=False)
    try:
        best = get_best_image_url(img_url, None, try_high_res=True)
        ct, cl = f.head_metadata(best, delay=delay)
        if ct and not ct.startswith("image/"):
            best = img_url
            ct, cl = f.head_metadata(img_url, delay=delay)
        return (img_url, best, ct, cl) if ct else None
    finally:
        if not use_shared:
            f.close()


def map_page(
    url: str,
    fetcher: Fetcher,
    want: set[str],
    limit_pdfs: int | None,
    limit_images: int | None,
    min_image_size: int | None,
    max_image_size: int | None,
    delay: float,
    head_workers: int = 4,
    same_domain: str | None = None,
    use_browser: bool = False,
) -> MapResult:
    """
    Map a page: fetch HTML, parse URLs, HEAD images for size filter. No downloads.
    Returns URLs to scrape; scrape phase runs strategically in parallel.
    """
    domain = sanitize_domain(url)
    raw, charset = fetcher.fetch_html(url, delay=delay)
    try:
        html_str = raw.decode(charset, errors="replace")
    except Exception:
        html_str = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "lxml")

    page_links = find_page_links(soup, url, same_domain or urlparse(url).netloc)

    pdf_urls: list[str] = []
    if "pdf" in want:
        for u in find_pdf_urls(soup, url):
            if limit_pdfs is not None and len(pdf_urls) >= limit_pdfs:
                break
            pdf_urls.append(u)

    image_items: list[tuple[str, str, str]] = []
    if "images" in want:
        # Manifest URLs return JSON; use httpx (not browser) so we get raw JSON
        def fetch_manifest(u: str) -> bytes:
            if use_browser:
                with Fetcher(use_browser=False) as f:
                    return f.fetch_html(u, delay=0)[0]
            return fetcher.fetch_html(u, delay=delay)[0]
        img_urls = collect_image_urls(soup, url, html_str, fetch_manifest=fetch_manifest, limit=limit_images)

        need_size_filter = min_image_size is not None or max_image_size is not None

        if not need_size_filter:
            for u in img_urls:
                best = get_best_image_url(u, None, try_high_res=True)
                image_items.append((u, best, "image"))
        else:
            effective_head_workers = 1 if use_browser else head_workers
            if effective_head_workers > 1 and len(img_urls) > 4:
                _head = lambda u: head_one_image(u, fetcher, delay, use_shared=False)
                with ThreadPoolExecutor(max_workers=min(effective_head_workers, len(img_urls) or 1)) as ex:
                    results = list(ex.map(_head, img_urls))
            else:
                results = [head_one_image(u, fetcher, delay, use_shared=True) for u in img_urls]

            for r in results:
                if r is None:
                    continue
                img_url, best_url, ct, content_length = r
                if content_length is not None:
                    if min_image_size is not None and content_length < min_image_size:
                        continue
                    if max_image_size is not None and content_length > max_image_size:
                        continue
                image_items.append((img_url, best_url, ct or "image"))

    text: tuple[str, str] | None = None
    if "text" in want:
        extracted = extract_text(soup, html_str)
        if extracted.strip():
            text = (url, extracted)

    return MapResult(page_links=page_links, pdf_urls=pdf_urls, image_items=image_items, text=text)


def scrape_assets(
    result: MapResult,
    fetcher_context: Callable[[], ContextManager[Fetcher]],
    out_dir: Path,
    domain: str,
    manifest: dict,
    workers: int,
    delay: float,
    use_browser: bool,
    progress_callback: Callable[[str | tuple], None] | None = None,
) -> None:
    """
    Download mapped PDFs and images in parallel (when not use_browser).
    Write text inline (no download). Calls progress_callback(("total", n)) once, then per asset.
    """
    urls_map = manifest.setdefault("urls", {})
    types_map = manifest.setdefault("types", {})

    def _exists(url: str, kind: str, ct: str | None = None) -> bool:
        key = (url, kind, ct or "")
        if key not in _exists._cache:
            _exists._cache[key] = path_exists_for_resource(out_dir, domain, url, kind, ct)
        return _exists._cache[key]

    _exists._cache: dict[tuple[str, str, str], bool] = {}

    if result.text:
        url, text = result.text
        if _exists(url, "text"):
            urls_map[url] = str(path_for_text_canonical(out_dir, domain, url))
            types_map[url] = "text/plain"
        elif url not in urls_map:
            dest = path_for_text(out_dir, domain, url)
            write_text(dest, text)
            urls_map[url] = str(dest)
            types_map[url] = "text/plain"
            if progress_callback:
                progress_callback("text")
            print(f"  Text: {dest}", file=sys.stderr)

    work: list[tuple[str, str, str | None]] = []  # (url, best_url, ct)
    for u in result.pdf_urls:
        if u not in urls_map:
            work.append((u, u, "application/pdf"))
    for img_url, best_url, ct in result.image_items:
        if img_url not in urls_map:
            work.append((img_url, best_url, ct))

    n_pdf = sum(1 for _, _, ct in work if ct == "application/pdf")
    n_img = len(work) - n_pdf
    n_text = 1 if result.text and not _exists(result.text[0], "text") else 0
    total_assets = n_text + len(work)
    if total_assets > 0:
        parts = []
        if n_text:
            parts.append("text")
        if n_pdf:
            parts.append(f"{n_pdf} PDFs")
        if n_img:
            parts.append(f"{n_img} images")
        print(f"  → Downloading {total_assets} assets ({', '.join(parts)})...", file=sys.stderr)
    if progress_callback and total_assets > 0:
        progress_callback(("total", total_assets))

    done_count: list[int] = [0]  # mutable for closure
    done_lock = threading.Lock()
    n_work = len(work)

    def _download_one(item: tuple[str, str, str | None], stagger_delay: float = 0) -> bool:
        if stagger_delay > 0:
            time.sleep(stagger_delay)
        url, best_url, ct = item
        if url in urls_map:
            return True
        is_pdf = ct == "application/pdf"
        canon = path_for_pdf_canonical(out_dir, domain, url) if is_pdf else path_for_image_canonical(out_dir, domain, url, ct)
        if canon.exists():
            with fetcher_context() as f:
                if _should_skip_existing_by_size(f, best_url, canon, delay=delay):
                    urls_map[url] = str(canon)
                    types_map[url] = ct or "image"
                    return True
            dest = canon  # overwrite when size differs
        else:
            dest = path_for_pdf(out_dir, domain, best_url) if is_pdf else path_for_image(out_dir, domain, best_url, ct)
        try:
            with fetcher_context() as f:
                f.fetch_binary(best_url, dest, delay=delay)
                urls_map[url] = str(dest)
                types_map[url] = ct or "image"
                return True
        except Exception:
            if best_url != url and not is_pdf:
                try:
                    with fetcher_context() as f:
                        f.fetch_binary(url, dest, delay=delay)
                    urls_map[url] = str(dest)
                    types_map[url] = ct or "image"
                    return True
                except Exception:
                    return False
            return False

    effective_workers = _effective_asset_workers(work, workers, use_browser)
    stagger = (delay / effective_workers) if effective_workers > 1 else 0
    def _progress_msg(ct: str, ok: bool, url: str, best_url: str) -> str:
        with done_lock:
            n = done_count[0]
        prefix = f"  [{n}/{n_work}] " if n_work > 1 else "  "
        if ct == "application/pdf":
            return f"{prefix}PDF: {best_url}" if ok else f"{prefix}PDF fail {best_url}"
        return f"{prefix}Image: {best_url}" if ok else f"{prefix}Image fail {url}"

    if effective_workers == 1:
        for item in work:
            ok = _download_one(item, stagger_delay=0)
            if ok and progress_callback:
                progress_callback("asset")
            url, best_url, ct = item
            with done_lock:
                done_count[0] += 1
            print(_progress_msg(ct, ok, url, best_url), file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as ex:
            futures = {
                ex.submit(_download_one, item, stagger * (i % effective_workers)): item
                for i, item in enumerate(work)
            }
            for fut in as_completed(futures):
                item = futures[fut]
                url, best_url, ct = item
                ok = fut.result()
                if ok and progress_callback:
                    progress_callback("asset")
                with done_lock:
                    done_count[0] += 1
                print(_progress_msg(ct, ok, url, best_url), file=sys.stderr)


def scrape_page(
    url: str,
    out_dir: Path,
    delay: float,
    manifest: dict,
    fetcher: Fetcher,
    limit_pdfs: int | None,
    limit_images: int | None,
    collect_links: bool,
    types: set[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    min_image_size: int | None = None,
    max_image_size: int | None = None,
    same_domain_for_links: str | None = ...,  # None = all links; str = filter; omit = page domain
    asset_workers: int = 1,
) -> list[str]:
    """
    Scrape a single page: PDFs, text, images (according to types).
    Returns page_links only when collect_links is True (crawl mode).
    When asset_workers > 1, PDF and image downloads run in parallel within the page.
    """
    want = types or VALID_TYPES
    domain = sanitize_domain(url)
    mf_path = manifest_path(out_dir, domain)
    urls_map = manifest.setdefault("urls", {})
    types_map = manifest.setdefault("types", {})

    # Fetch HTML
    print("  → Fetching page...", file=sys.stderr)
    raw, charset = fetcher.fetch_html(url, delay=delay)
    try:
        html_str = raw.decode(charset, errors="replace")
    except Exception:
        html_str = raw.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html_str, "lxml")

    # Build PDF work list
    pdf_work: list[tuple[str, Path]] = []
    if "pdf" in want:
        for pdf_url in find_pdf_urls(soup, url):
            if limit_pdfs is not None and len(pdf_work) >= limit_pdfs:
                break
            if pdf_url in urls_map:
                continue
            canon = path_for_pdf_canonical(out_dir, domain, pdf_url)
            dest = canon if canon.exists() else path_for_pdf(out_dir, domain, pdf_url)
            pdf_work.append((pdf_url, dest))

    # Text pipeline (single item, no parallelization)
    if "text" in want:
        text = extract_text(soup, html_str)
        if text.strip():
            if path_exists_for_resource(out_dir, domain, url, "text"):
                dest = path_for_text_canonical(out_dir, domain, url)
                urls_map[url] = str(dest)
                types_map[url] = "text/plain"
            else:
                dest = path_for_text(out_dir, domain, url)
                write_text(dest, text)
                urls_map[url] = str(dest)
                types_map[url] = "text/plain"
                if progress_callback:
                    progress_callback("text")
                print(f"  Text: {dest}", file=sys.stderr)

    # Build image work list (url for urls_map key, best_url to fetch, ct, dest)
    image_work: list[tuple[str, str, str, Path]] = []
    need_size_filter = min_image_size is not None or max_image_size is not None
    if "images" in want:
        fetch_manifest = lambda u: fetcher.fetch_bytes(u, delay=delay)
        for img_url in collect_image_urls(soup, url, html_str, fetch_manifest=fetch_manifest, limit=None):
            if limit_images is not None and len(image_work) >= limit_images:
                break
            if img_url in urls_map:
                continue
            best_url = get_best_image_url(img_url, None, try_high_res=True)
            ct: str | None = "image"
            content_length: int | None = None
            if need_size_filter:
                r = head_one_image(img_url, fetcher, delay, use_shared=True)
                if r is None:
                    continue
                _, best_url, ct, content_length = r
                if content_length is not None:
                    if min_image_size is not None and content_length < min_image_size:
                        continue
                    if max_image_size is not None and content_length > max_image_size:
                        continue
            canon = path_for_image_canonical(out_dir, domain, img_url, ct)
            dest = canon if canon.exists() else path_for_image(out_dir, domain, best_url, ct)
            image_work.append((img_url, best_url, ct or "image", dest))

    # Run PDF + image downloads (parallel when asset_workers > 1)
    # Work items: (fetch_url, dest, ct, map_key) for urls_map[map_key] = str(dest)
    asset_tasks: list[tuple[str, Path, str, str]] = []
    for pdf_url, dest in pdf_work:
        asset_tasks.append((pdf_url, dest, "application/pdf", pdf_url))
    for img_url, best_url, ct, dest in image_work:
        asset_tasks.append((best_url, dest, ct, img_url))

    if not asset_tasks:
        pass
    elif asset_workers <= 1:
        for fetch_url, dest, ct, map_key in asset_tasks:
            if dest.exists() and _should_skip_existing_by_size(fetcher, fetch_url, dest, delay=delay):
                urls_map[map_key] = str(dest)
                types_map[map_key] = ct
                if progress_callback:
                    progress_callback("pdf" if ct == "application/pdf" else "image")
                print(f"  PDF: {fetch_url}" if ct == "application/pdf" else f"  Image: {fetch_url}", file=sys.stderr)
                continue
            try:
                fetcher.fetch_binary(fetch_url, dest, delay=delay)
                urls_map[map_key] = str(dest)
                types_map[map_key] = ct
                if progress_callback:
                    progress_callback("pdf" if ct == "application/pdf" else "image")
                print(f"  PDF: {fetch_url}" if ct == "application/pdf" else f"  Image: {fetch_url}", file=sys.stderr)
            except Exception as e:
                if ct != "application/pdf" and map_key != fetch_url:
                    try:
                        fetcher.fetch_binary(map_key, dest, delay=delay)
                        urls_map[map_key] = str(dest)
                        types_map[map_key] = ct
                        if progress_callback:
                            progress_callback("image")
                        print(f"  Image: {map_key}", file=sys.stderr)
                    except Exception as inner_e:
                        print(f"  Image fail {map_key}: {inner_e}", file=sys.stderr)
                else:
                    print(f"  {'PDF' if ct == 'application/pdf' else 'Image'} fail {map_key}: {e}", file=sys.stderr)
    else:
        effective = _effective_asset_workers_for_tasks(asset_tasks, asset_workers)
        stagger = (delay / effective) if effective > 1 else 0
        manifest_lock = threading.Lock()
        _thread_local = threading.local()
        _fetchers_to_close: list[Fetcher] = []
        _fetchers_lock = threading.Lock()

        def _init_worker() -> None:
            f = fetcher.spawn()
            with _fetchers_lock:
                _fetchers_to_close.append(f)
            _thread_local.fetcher = f

        def _get_thread_fetcher() -> Fetcher:
            f = getattr(_thread_local, "fetcher", None)
            if f is None:
                f = fetcher.spawn()
                with _fetchers_lock:
                    _fetchers_to_close.append(f)
                _thread_local.fetcher = f
            return f

        def _download_asset(item: tuple[str, Path, str, str], stagger_delay: float) -> tuple[str, Path, str, str] | None:
            thread_fetcher = _get_thread_fetcher()
            fetch_url, dest, ct, map_key = item
            time.sleep(stagger_delay)
            if dest.exists() and _should_skip_existing_by_size(thread_fetcher, fetch_url, dest, delay=0):
                return (map_key, dest, ct, "pdf" if ct == "application/pdf" else "image")
            try:
                thread_fetcher.fetch_binary(fetch_url, dest, delay=0)
                return (map_key, dest, ct, "pdf" if ct == "application/pdf" else "image")
            except Exception as e:
                if ct != "application/pdf" and map_key != fetch_url:
                    try:
                        thread_fetcher.fetch_binary(map_key, dest, delay=0)
                        return (map_key, dest, ct, "image")
                    except Exception as inner_e:
                        return (map_key, dest, ct, f"fail:{inner_e!s}")
                return (map_key, dest, ct, f"fail:{e!s}")

        try:
            with ThreadPoolExecutor(max_workers=effective, initializer=_init_worker) as ex:
                futures = {
                    ex.submit(_download_asset, item, stagger * (i % effective)): item
                    for i, item in enumerate(asset_tasks)
                }
                for fut in as_completed(futures):
                    fetch_url, dest, ct, map_key = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        print(f"  {'PDF' if ct == 'application/pdf' else 'Image'} fail {map_key}: {e}", file=sys.stderr)
                        continue
                    if result is None:
                        continue
                    _map_key, _dest, _ct, status = result
                    with manifest_lock:
                        urls_map[_map_key] = str(_dest)
                        types_map[_map_key] = _ct
                    if status.startswith("fail:"):
                        print(f"  {'PDF' if _ct == 'application/pdf' else 'Image'} fail {_map_key}: {status[5:]}", file=sys.stderr)
                    else:
                        if progress_callback:
                            progress_callback(status)
                        print(f"  PDF: {_map_key}" if _ct == "application/pdf" else f"  Image: {_map_key}", file=sys.stderr)
        finally:
            for f in _fetchers_to_close:
                try:
                    f.close()
                except Exception:
                    pass

    save_manifest(mf_path, manifest)

    if not collect_links:
        return []
    domain_filter = urlparse(url).netloc if same_domain_for_links is ... else same_domain_for_links
    return find_page_links(soup, url, domain_filter)


def run_done_script(cmd: str, out_dir: Path) -> None:
    """Run shell command with {out_dir} placeholder. Ignores errors."""
    import subprocess
    if not cmd or not cmd.strip():
        return
    cmd = cmd.strip().replace("{out_dir}", str(out_dir.resolve()))
    try:
        subprocess.run(cmd, shell=True, check=False)
    except Exception as e:
        print(f"  Done-script error: {e}", file=sys.stderr)


def run_single_or_sequential_crawl(
    args: "argparse.Namespace",
    out_dir: Path,
    limit: int | None,
    types_set: set[str] | None,
    workers: int,
    use_progress: bool,
    min_image_size: int | None,
    max_image_size: int | None,
) -> None:
    """Single-page scrape or sequential crawl (workers=1)."""
    with Fetcher(
        use_browser=args.js,
        flaresolverr_url=getattr(args, "flaresolverr_url", None),
        headed=getattr(args, "headed", False),
        human_bypass=getattr(args, "human_bypass", False),
    ) as fetcher:
        if args.crawl:
            start_domain = urlparse(args.url).netloc
            same_domain_only = args.same_domain_only
            retried_cross_domain = False

            def run_crawl() -> set[str]:
                print(f"  → Crawl started (max depth {args.max_depth})...", file=sys.stderr)
                print(CRAWL_TIP, file=sys.stderr)
                pbar = tqdm(desc="Crawl", unit=" page", file=sys.stderr, disable=not use_progress)
                q: deque[tuple[str, int]] = deque([(args.url, 0)])
                seen: set[str] = set()
                link_filter = start_domain if same_domain_only else None
                while q:
                    url, depth = q.popleft()
                    if url in seen or depth > args.max_depth:
                        continue
                    if same_domain_only and urlparse(url).netloc != start_domain:
                        continue
                    if not getattr(args, "no_robots", False) and not can_fetch(url):
                        print(f"Skip (robots): {url}", file=sys.stderr)
                        continue
                    seen.add(url)
                    print(f"\n[{depth}] {url}", file=sys.stderr)
                    domain = sanitize_domain(url)
                    manifest = load_manifest(manifest_path(out_dir, domain))
                    try:
                        links = scrape_page(
                            url, out_dir, args.delay, manifest, fetcher,
                            limit, limit, collect_links=True, types=types_set,
                            progress_callback=None,
                            min_image_size=min_image_size,
                            max_image_size=max_image_size,
                            same_domain_for_links=link_filter,
                        )
                        if use_progress:
                            pbar.set_postfix(queue=len(q))
                            pbar.update(1)
                        for link in links:
                            if link not in seen and (
                                not same_domain_only or urlparse(link).netloc == start_domain
                            ):
                                q.append((link, depth + 1))
                    except Exception as e:
                        print(f"Error {url}: {e}", file=sys.stderr)
                if use_progress:
                    pbar.close()
                return seen

            seen = run_crawl()
            if (
                same_domain_only
                and not retried_cross_domain
                and len(seen) <= 1
            ):
                print(
                    "\nCrawl returned no results (same-domain); retrying with cross-domain...",
                    file=sys.stderr,
                )
                same_domain_only = False
                retried_cross_domain = True
                seen = run_crawl()
        else:
            if not getattr(args, "no_robots", False) and not can_fetch(args.url):
                print("robots.txt disallows this URL.", file=sys.stderr)
                sys.exit(1)
            domain = sanitize_domain(args.url)
            manifest = load_manifest(manifest_path(out_dir, domain))
            max_iterations = max(1, getattr(args, "max_iterations", 3))
            had_403 = False
            last_exc: BaseException | None = None
            for iteration in range(max_iterations):
                delay_i = args.delay * (ITERATION_DELAY_FACTOR ** iteration)
                timeout_i = min(
                    DEFAULT_TIMEOUT * (ITERATION_TIMEOUT_FACTOR ** iteration),
                    MAX_TIMEOUT,
                )
                use_browser = args.js or (iteration > 0 and had_403)
                if iteration > 0:
                    suffix = "; browser" if use_browser else ""
                    print(
                        f"Iteration {iteration + 1}/{max_iterations} (timeout={timeout_i:.0f}s, delay={delay_i:.1f}s{suffix})",
                        file=sys.stderr,
                    )
                else:
                    print(f"Scrape: {args.url}", file=sys.stderr)
                progress_cb: Callable[[str | tuple], None] | None = None
                pbar = None
                if use_progress:
                    pbar = tqdm(desc="Scraping", unit=" asset", file=sys.stderr)
                    def _progress_cb(msg):
                        if isinstance(msg, tuple) and msg[0] == "total":
                            pbar.reset(total=msg[1])
                        else:
                            pbar.update(1)
                    progress_cb = _progress_cb
                try:
                    with Fetcher(
                        timeout=timeout_i,
                        use_browser=use_browser,
                        flaresolverr_url=getattr(args, "flaresolverr_url", None),
                        headed=getattr(args, "headed", False),
                        human_bypass=getattr(args, "human_bypass", False),
                    ) as iter_fetcher:
                        want = types_set or VALID_TYPES
                        if getattr(args, "map_first", True):
                            print("  → Fetching and mapping page...", file=sys.stderr)
                            map_result = map_page(
                                args.url,
                                iter_fetcher,
                                want,
                                limit, limit,
                                min_image_size, max_image_size,
                                delay_i,
                                head_workers=min(SAFE_HEAD_WORKERS, workers),
                                use_browser=use_browser,
                            )
                            fetcher_ctx = (
                                (lambda f: lambda: nullcontext(f))(iter_fetcher)
                                if use_browser
                                else (lambda: Fetcher(timeout=60, use_browser=False, flaresolverr_url=getattr(args, "flaresolverr_url", None)))
                            )
                            n_pdf, n_img = len(map_result.pdf_urls), len(map_result.image_items)
                            if n_pdf or n_img or map_result.text:
                                parts = []
                                if map_result.text:
                                    parts.append("text")
                                if n_pdf:
                                    parts.append(f"{n_pdf} PDFs")
                                if n_img:
                                    parts.append(f"{n_img} images")
                                print(f"  Found: {', '.join(parts)}", file=sys.stderr)
                            scrape_assets(
                                map_result,
                                fetcher_ctx,
                                out_dir,
                                domain,
                                manifest,
                                workers,
                                delay_i,
                                use_browser,
                                progress_cb,
                            )
                            save_manifest(manifest_path(out_dir, domain), manifest)
                        else:
                            print("  → Fetching and extracting page...", file=sys.stderr)
                            scrape_page(
                                args.url, out_dir, delay_i, manifest, iter_fetcher,
                                limit, limit, collect_links=False, types=types_set,
                                progress_callback=progress_cb,
                                min_image_size=min_image_size,
                                max_image_size=max_image_size,
                            )
                except Exception as e:
                    last_exc = e
                    if is_403(e) and iteration < max_iterations - 1:
                        had_403 = True
                        print(f"  Retrying after 403 (iteration {iteration + 1})...", file=sys.stderr)
                        if pbar is not None:
                            pbar.close()
                        continue
                    if pbar is not None:
                        pbar.close()
                    raise
                finally:
                    if pbar is not None:
                        pbar.close()
                break


def crawl_parallel(
    start_url: str,
    out_dir: Path,
    delay: float,
    max_depth: int,
    same_domain_only: bool,
    limit: int | None,
    types_set: set[str] | None,
    workers: int,
    use_progress: bool,
    min_image_size: int | None,
    max_image_size: int | None,
    *,
    use_browser: bool = False,
    flaresolverr_url: str | None = None,
    no_robots: bool = False,
    headed: bool = False,
    human_bypass: bool = False,
) -> None:
    """Crawl with a thread pool; each worker uses its own Fetcher, shared manifest lock."""
    start_domain = urlparse(start_url).netloc
    retried_cross_domain = False

    def run_crawl(same_dom: bool) -> set[str]:
        print(f"  → Crawl started (max depth {max_depth}, {workers} workers)...", file=sys.stderr)
        print(CRAWL_TIP, file=sys.stderr)
        link_filter = start_domain if same_dom else None
        work_queue: Queue[tuple[str, int] | None] = Queue()
        seen: set[str] = set()
        seen_lock = threading.Lock()
        manifest_lock = threading.Lock()
        pending = 0
        pending_lock = threading.Lock()
        pbar = tqdm(desc="Crawl", unit=" page", file=sys.stderr) if (tqdm and use_progress) else None

        def process_one(url: str, depth: int, fetcher: Fetcher) -> list[str]:
            if not no_robots and not can_fetch(url):
                return []
            domain = sanitize_domain(url)
            with manifest_lock:
                manifest = load_manifest(manifest_path(out_dir, domain))
                try:
                    links = scrape_page(
                        url, out_dir, delay, manifest, fetcher,
                        limit, limit, collect_links=True, types=types_set,
                        progress_callback=None,
                        min_image_size=min_image_size,
                        max_image_size=max_image_size,
                        same_domain_for_links=link_filter,
                        asset_workers=min(SAFE_ASSET_WORKERS, workers),
                    )
                except Exception as e:
                    print(f"Error {url}: {e}", file=sys.stderr)
                    return []
                save_manifest(manifest_path(out_dir, domain), manifest)
            return links

        def worker() -> None:
            nonlocal pending
            fetcher = Fetcher(
                use_browser=use_browser,
                flaresolverr_url=flaresolverr_url,
                headed=headed,
                human_bypass=human_bypass,
            )
            try:
                while True:
                    item = work_queue.get()
                    if item is None:
                        return
                    url, depth = item
                    if depth > max_depth:
                        with pending_lock:
                            pending -= 1
                            if pending == 0:
                                for _ in range(workers):
                                    work_queue.put(None)
                        continue
                    with seen_lock:
                        if url in seen:
                            with pending_lock:
                                pending -= 1
                                if pending == 0:
                                    for _ in range(workers):
                                        work_queue.put(None)
                            continue
                        seen.add(url)
                    print(f"\n[{depth}] {url}", file=sys.stderr)
                    try:
                        links = process_one(url, depth, fetcher)
                    finally:
                        with pending_lock:
                            pending -= 1
                            if pbar is not None:
                                pbar.set_postfix(pending=pending)
                            if pending == 0:
                                for _ in range(workers):
                                    work_queue.put(None)
                        if pbar is not None:
                            pbar.update(1)
                    for link in links:
                        if same_dom and urlparse(link).netloc != start_domain:
                            continue
                        with seen_lock:
                            if link in seen:
                                continue
                            seen.add(link)
                        work_queue.put((link, depth + 1))
                        with pending_lock:
                            pending += 1
            finally:
                fetcher.close()

        with pending_lock:
            pending = 1
        work_queue.put((start_url, 0))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futs = [executor.submit(worker) for _ in range(workers)]
            for f in as_completed(futs):
                f.result()
        if pbar is not None:
            pbar.close()
        return seen

    seen = run_crawl(same_domain_only)
    if same_domain_only and not retried_cross_domain and len(seen) <= 1:
        print(
            "\nCrawl returned no results (same-domain); retrying with cross-domain...",
            file=sys.stderr,
        )
        retried_cross_domain = True
        run_crawl(False)
