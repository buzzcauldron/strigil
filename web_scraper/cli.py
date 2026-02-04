"""CLI entry point for the scraper. Invoked as `scrape` when installed with pip install -e ."""

import argparse
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from web_scraper._deps import check_required, optional_hint
from web_scraper.extractors import (
    find_image_urls,
    find_page_links,
    find_pdf_urls,
    extract_text,
    get_best_image_url,
)
from web_scraper.fetcher import fetch_binary, fetch_html, head_content_type
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


def _scrape_page(
    url: str,
    out_dir: Path,
    delay: float,
    manifest: dict,
    limit_pdfs: int | None,
    limit_images: int | None,
) -> tuple[list[str], list[str]]:
    """
    Scrape a single page: PDFs, text, images. Returns (page_links, pdf_urls_found).
    """
    domain = sanitize_domain(url)
    mf_path = manifest_path(out_dir, domain)

    # Fetch HTML
    raw, charset = fetch_html(url, delay=delay)
    try:
        html_str = raw.decode(charset, errors="replace")
    except Exception:
        html_str = raw.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html_str, "lxml")
    base_url = url

    # PDF pipeline
    pdf_urls = find_pdf_urls(soup, base_url)
    pdf_count = 0
    for pdf_url in pdf_urls:
        if limit_pdfs is not None and pdf_count >= limit_pdfs:
            break
        if manifest.get("urls", {}).get(pdf_url):
            continue
        dest = path_for_pdf(out_dir, domain, pdf_url)
        if dest.exists():
            manifest.setdefault("urls", {})[pdf_url] = str(dest)
            continue
        try:
            fetch_binary(pdf_url, dest, delay=delay)
            manifest.setdefault("urls", {})[pdf_url] = str(dest)
            manifest.setdefault("types", {})[pdf_url] = "application/pdf"
            pdf_count += 1
            print(f"  PDF: {pdf_url}", file=sys.stderr)
        except Exception as e:
            print(f"  PDF fail {pdf_url}: {e}", file=sys.stderr)

    # Text pipeline
    text = extract_text(soup, html_str)
    if text.strip():
        dest = path_for_text(out_dir, domain, url)
        write_text(dest, text)
        manifest.setdefault("urls", {})[url] = str(dest)
        manifest.setdefault("types", {})[url] = "text/plain"
        print(f"  Text: {dest}", file=sys.stderr)

    # Image pipeline
    img_urls = find_image_urls(soup, base_url)
    img_count = 0
    for img_url in img_urls:
        if limit_images is not None and img_count >= limit_images:
            break
        if manifest.get("urls", {}).get(img_url):
            continue
        best_url = get_best_image_url(img_url, None, try_high_res=True)
        ct = head_content_type(best_url, delay=delay)
        if ct and not ct.startswith("image/"):
            best_url = img_url
            ct = head_content_type(img_url, delay=delay)
        dest = path_for_image(out_dir, domain, best_url, ct)
        if dest.exists():
            manifest.setdefault("urls", {})[img_url] = str(dest)
            continue
        try:
            fetch_binary(best_url, dest, delay=delay)
            manifest.setdefault("urls", {})[img_url] = str(dest)
            manifest.setdefault("types", {})[img_url] = ct or "image"
            img_count += 1
            print(f"  Image: {best_url}", file=sys.stderr)
        except Exception as e:
            if best_url != img_url:
                try:
                    fetch_binary(img_url, dest, delay=delay)
                    manifest.setdefault("urls", {})[img_url] = str(dest)
                    img_count += 1
                except Exception:
                    print(f"  Image fail {img_url}: {e}", file=sys.stderr)
            else:
                print(f"  Image fail {img_url}: {e}", file=sys.stderr)

    save_manifest(mf_path, manifest)

    # Links for crawl
    same_domain = urlparse(url).netloc
    page_links = find_page_links(soup, base_url, same_domain)
    return page_links, pdf_urls


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
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    limit = args.limit

    if args.crawl:
        # BFS crawl
        start_domain = urlparse(args.url).netloc
        queue: deque[tuple[str, int]] = deque([(args.url, 0)])
        seen: set[str] = set()
        while queue:
            url, depth = queue.popleft()
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
                links, _ = _scrape_page(url, out_dir, args.delay, manifest, limit, limit)
                for link in links:
                    if link not in seen and (not args.same_domain_only or urlparse(link).netloc == start_domain):
                        queue.append((link, depth + 1))
            except Exception as e:
                print(f"Error {url}: {e}", file=sys.stderr)
    else:
        # Single page
        domain = sanitize_domain(args.url)
        manifest = load_manifest(manifest_path(out_dir, domain))
        if not can_fetch(args.url):
            print("robots.txt disallows this URL.", file=sys.stderr)
            sys.exit(1)
        print(f"Scrape: {args.url}", file=sys.stderr)
        _scrape_page(args.url, out_dir, args.delay, manifest, limit, limit)

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
