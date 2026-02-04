"""CLI entry point for the scraper. Invoked as `scrape` when installed with pip install -e ."""

import argparse
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from web_scraper._deps import check_required, optional_hint
from web_scraper.extractors import (
    find_image_urls,
    find_page_links,
    find_pdf_urls,
    extract_text,
    get_best_image_url,
)
from web_scraper.fetcher import Fetcher
from web_scraper.hardware import default_workers
from web_scraper.robots import can_fetch
from web_scraper.storage import (
    load_manifest,
    manifest_path,
    path_for_image,
    path_for_pdf,
    path_for_text,
    sanitize_domain,
    save_manifest,
    write_text,
)


VALID_TYPES = frozenset({"pdf", "text", "images"})


def _parse_size(s: str) -> int:
    """Parse size string to bytes: 100, 100k, 1m (case-insensitive)."""
    s = s.strip().lower()
    if not s:
        raise ValueError("empty size")
    if s.endswith("k"):
        return int(s[:-1]) * 1024
    if s.endswith("m"):
        return int(s[:-1]) * 1024 * 1024
    return int(s)


def _scrape_page(
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
) -> list[str]:
    """
    Scrape a single page: PDFs, text, images (according to types).
    Returns page_links only when collect_links is True (crawl mode).
    """
    want = types or VALID_TYPES
    domain = sanitize_domain(url)
    mf_path = manifest_path(out_dir, domain)
    urls_map = manifest.setdefault("urls", {})
    types_map = manifest.setdefault("types", {})

    # Fetch HTML
    raw, charset = fetcher.fetch_html(url, delay=delay)
    try:
        html_str = raw.decode(charset, errors="replace")
    except Exception:
        html_str = raw.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html_str, "lxml")

    # PDF pipeline
    pdf_count = 0
    if "pdf" in want:
        for pdf_url in find_pdf_urls(soup, url):
            if limit_pdfs is not None and pdf_count >= limit_pdfs:
                break
            if pdf_url in urls_map:
                continue
            dest = path_for_pdf(out_dir, domain, pdf_url)
            if dest.exists():
                urls_map[pdf_url] = str(dest)
                continue
            try:
                fetcher.fetch_binary(pdf_url, dest, delay=delay)
                urls_map[pdf_url] = str(dest)
                types_map[pdf_url] = "application/pdf"
                pdf_count += 1
                if progress_callback:
                    progress_callback("pdf")
                print(f"  PDF: {pdf_url}", file=sys.stderr)
            except Exception as e:
                print(f"  PDF fail {pdf_url}: {e}", file=sys.stderr)

    # Text pipeline
    if "text" in want:
        text = extract_text(soup, html_str)
        if text.strip():
            dest = path_for_text(out_dir, domain, url)
            write_text(dest, text)
            urls_map[url] = str(dest)
            types_map[url] = "text/plain"
            if progress_callback:
                progress_callback("text")
            print(f"  Text: {dest}", file=sys.stderr)

    # Image pipeline
    img_count = 0
    if "images" in want:
        for img_url in find_image_urls(soup, url):
            if limit_images is not None and img_count >= limit_images:
                break
            if img_url in urls_map:
                continue
            best_url = get_best_image_url(img_url, None, try_high_res=True)
            ct, content_length = fetcher.head_metadata(best_url, delay=delay)
            if ct and not ct.startswith("image/"):
                best_url = img_url
                ct, content_length = fetcher.head_metadata(img_url, delay=delay)
            if content_length is not None:
                if min_image_size is not None and content_length < min_image_size:
                    continue
                if max_image_size is not None and content_length > max_image_size:
                    continue
            dest = path_for_image(out_dir, domain, best_url, ct)
            if dest.exists():
                urls_map[img_url] = str(dest)
                continue
            try:
                fetcher.fetch_binary(best_url, dest, delay=delay)
                urls_map[img_url] = str(dest)
                types_map[img_url] = ct or "image"
                img_count += 1
                if progress_callback:
                    progress_callback("image")
                print(f"  Image: {best_url}", file=sys.stderr)
            except Exception as e:
                if best_url != img_url:
                    try:
                        fetcher.fetch_binary(img_url, dest, delay=delay)
                        urls_map[img_url] = str(dest)
                        img_count += 1
                        if progress_callback:
                            progress_callback("image")
                    except Exception:
                        print(f"  Image fail {img_url}: {e}", file=sys.stderr)
                else:
                    print(f"  Image fail {img_url}: {e}", file=sys.stderr)

    save_manifest(mf_path, manifest)

    if not collect_links:
        return []
    return find_page_links(soup, url, urlparse(url).netloc)


def main() -> None:
    check_required()
    hint = optional_hint()
    if hint:
        print(hint, file=sys.stderr)

    parser = argparse.ArgumentParser(
        prog="scrape",
        description="Scrape PDFs, text, and images from a URL and store locally.",
    )
    parser.add_argument("--url", required=True, help="Start URL to scrape")
    parser.add_argument("--out-dir", default="output", help="Output directory (default: output)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--crawl", action="store_true", help="Enable crawl mode (follow links)")
    parser.add_argument("--max-depth", type=int, default=2, help="Max crawl depth when --crawl")
    parser.add_argument("--same-domain-only", action="store_true", help="Only follow same-domain links")
    parser.add_argument("--limit", type=int, default=None, help="Max PDFs/images per page (for testing)")
    parser.add_argument(
        "--types",
        nargs="*",
        default=None,
        choices=list(VALID_TYPES),
        metavar="TYPE",
        help="File types to scrape: pdf, text, images (default: all)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=f"Parallel workers for crawl (default: auto from CPU, max {default_workers()})",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bar (e.g. for scripting)",
    )
    parser.add_argument(
        "--min-image-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="Skip images smaller than SIZE (e.g. 50k, 1m). Uses HEAD Content-Length.",
    )
    parser.add_argument(
        "--max-image-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="Skip images larger than SIZE (e.g. 5m, 10m). Uses HEAD Content-Length.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    limit = args.limit
    types_set = set(args.types) if args.types else None
    min_image_size: int | None = None
    max_image_size: int | None = None
    for opt, val in (("--min-image-size", args.min_image_size), ("--max-image-size", args.max_image_size)):
        if not val:
            continue
        try:
            parsed = _parse_size(val)
            if "min" in opt:
                min_image_size = parsed
            else:
                max_image_size = parsed
        except ValueError as e:
            parser.error(f"{opt}: {e}")
    workers = args.workers if args.workers is not None else default_workers()
    workers = max(1, min(workers, default_workers()))

    use_progress = not args.no_progress and tqdm is not None
    if args.crawl and workers > 1:
        _crawl_parallel(
            args.url, out_dir, args.delay, args.max_depth,
            args.same_domain_only, limit, types_set, workers, use_progress,
            min_image_size, max_image_size,
        )
    else:
        _run_single_or_sequential_crawl(
            args, out_dir, limit, types_set, workers, use_progress,
            min_image_size, max_image_size,
        )

    print("\nDone.", file=sys.stderr)


def _run_single_or_sequential_crawl(
    args: argparse.Namespace,
    out_dir: Path,
    limit: int | None,
    types_set: set[str] | None,
    workers: int,
    use_progress: bool,
    min_image_size: int | None,
    max_image_size: int | None,
) -> None:
    """Single-page scrape or sequential crawl (workers=1)."""
    with Fetcher() as fetcher:
        if args.crawl:
            pbar = tqdm(desc="Crawl", unit="page", file=sys.stderr, disable=not use_progress)
            start_domain = urlparse(args.url).netloc
            q: deque[tuple[str, int]] = deque([(args.url, 0)])
            seen: set[str] = set()
            while q:
                url, depth = q.popleft()
                if url in seen or depth > args.max_depth:
                    continue
                if args.same_domain_only and urlparse(url).netloc != start_domain:
                    continue
                if not can_fetch(url):
                    print(f"Skip (robots): {url}", file=sys.stderr)
                    continue
                seen.add(url)
                print(f"\n[{depth}] {url}", file=sys.stderr)
                domain = sanitize_domain(url)
                manifest = load_manifest(manifest_path(out_dir, domain))
                try:
                    links = _scrape_page(
                        url, out_dir, args.delay, manifest, fetcher,
                        limit, limit, collect_links=True, types=types_set,
                        progress_callback=None,
                        min_image_size=min_image_size,
                        max_image_size=max_image_size,
                    )
                    if use_progress:
                        pbar.update(1)
                    for link in links:
                        if link not in seen and (
                            not args.same_domain_only or urlparse(link).netloc == start_domain
                        ):
                            q.append((link, depth + 1))
                except Exception as e:
                    print(f"Error {url}: {e}", file=sys.stderr)
            if use_progress:
                pbar.close()
        else:
            if not can_fetch(args.url):
                print("robots.txt disallows this URL.", file=sys.stderr)
                sys.exit(1)
            domain = sanitize_domain(args.url)
            manifest = load_manifest(manifest_path(out_dir, domain))
            print(f"Scrape: {args.url}", file=sys.stderr)
            progress_cb: Callable[[str], None] | None = None
            if use_progress:
                pbar = tqdm(desc="Scraping", unit="asset", file=sys.stderr)
                progress_cb = lambda _: pbar.update(1)
            try:
                _scrape_page(
                    args.url, out_dir, args.delay, manifest, fetcher,
                    limit, limit, collect_links=False, types=types_set,
                    progress_callback=progress_cb,
                    min_image_size=min_image_size,
                    max_image_size=max_image_size,
                )
            finally:
                if use_progress:
                    pbar.close()


def _crawl_parallel(
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
) -> None:
    """Crawl with a thread pool; each worker uses its own Fetcher, shared manifest lock."""
    start_domain = urlparse(start_url).netloc
    work_queue: Queue[tuple[str, int] | None] = Queue()
    seen: set[str] = set()
    seen_lock = threading.Lock()
    manifest_lock = threading.Lock()
    pending = 0
    pending_lock = threading.Lock()
    pbar = tqdm(desc="Crawl", unit="page", file=sys.stderr) if (tqdm and use_progress) else None

    def process_one(url: str, depth: int) -> list[str]:
        if not can_fetch(url):
            return []
        with Fetcher() as fetcher:
            domain = sanitize_domain(url)
            with manifest_lock:
                manifest = load_manifest(manifest_path(out_dir, domain))
                try:
                    links = _scrape_page(
                        url, out_dir, delay, manifest, fetcher,
                        limit, limit, collect_links=True, types=types_set,
                        progress_callback=None,
                        min_image_size=min_image_size,
                        max_image_size=max_image_size,
                    )
                except Exception as e:
                    print(f"Error {url}: {e}", file=sys.stderr)
                    return []
                save_manifest(manifest_path(out_dir, domain), manifest)
        return links

    def worker() -> None:
        nonlocal pending
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
                links = process_one(url, depth)
            finally:
                if pbar:
                    pbar.update(1)
                with pending_lock:
                    pending -= 1
                    if pending == 0:
                        for _ in range(workers):
                            work_queue.put(None)
            for link in links:
                if same_domain_only and urlparse(link).netloc != start_domain:
                    continue
                with seen_lock:
                    if link in seen:
                        continue
                    seen.add(link)
                work_queue.put((link, depth + 1))
                with pending_lock:
                    pending += 1

    with pending_lock:
        pending = 1
    work_queue.put((start_url, 0))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futs = [executor.submit(worker) for _ in range(workers)]
        for f in as_completed(futs):
            f.result()
    if pbar:
        pbar.close()


if __name__ == "__main__":
    main()
