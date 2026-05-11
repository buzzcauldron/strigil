"""Scraping pipeline: map pages, scrape assets, crawl. Used by CLI and programmatic callers."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Callable, ContextManager
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from strigil.discovery import collect_image_entries
from strigil.schema import DiscoveryContext
from strigil.extractors import (
    extract_html_description_snippets,
    find_page_links,
    find_pdf_urls,
    extract_text,
    extract_text_html,
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
    path_for_text_html,
    path_for_text_html_canonical,
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
    work_items: list[tuple[str, str, str | None, dict[str, Any] | None]],
    requested: int,
    use_browser: bool,
) -> int:
    """Reduce parallelism when work is mostly large IIIF images (avoids timeouts)."""
    if use_browser or requested <= 1:
        return 1 if use_browser else requested
    image_items = [(u, b, ct) for u, b, ct, _ in work_items if ct != "application/pdf"]
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

    page_url: str = ""
    page_links: list[str] = field(default_factory=list)
    pdf_urls: list[str] = field(default_factory=list)
    image_items: list[tuple[str, str, str, dict[str, Any] | None]] = field(default_factory=list)
    text: tuple[str, str] | None = None  # (page_url, extracted_text) or None
    text_html: tuple[str, str] | None = None  # (page_url, main-content HTML) or None
    iiif_descriptions: list[dict[str, Any]] = field(default_factory=list)
    html_descriptions: list[dict[str, Any]] = field(default_factory=list)


def _maybe_collect_html_description(
    soup: BeautifulSoup,
    page_url: str,
    discovery_context: DiscoveryContext | None,
) -> None:
    """Append HTML meta/OG/JSON-LD snippets to discovery context when present."""
    if not discovery_context:
        return
    h = extract_html_description_snippets(soup, page_url)
    if h:
        discovery_context.html_descriptions.append(h)


def _merge_iiif_description_blocks(target: dict[str, Any], blocks: list[dict[str, Any]] | None) -> None:
    """Merge IIIF descriptive manifest records into manuscript target (dedupe by manifest URL or id)."""
    if not blocks:
        return
    cur = target.get("iiif_descriptions")
    if not isinstance(cur, list):
        cur = []
    seen = {(b.get("manifest_url") or b.get("id")) for b in cur if isinstance(b, dict)}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        key = b.get("manifest_url") or b.get("id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        cur.append(b)
    target["iiif_descriptions"] = cur


def _merge_html_description_snippets(target: dict[str, Any], blocks: list[dict[str, Any]] | None) -> None:
    """Merge HTML-derived description snippets (dedupe by page_url in sources)."""
    if not blocks:
        return
    cur = target.get("html_descriptions")
    if not isinstance(cur, list):
        cur = []
    seen_pages: set[str] = set()
    for b in cur:
        if isinstance(b, dict) and isinstance(b.get("sources"), dict):
            pu = b["sources"].get("page_url")
            if isinstance(pu, str) and pu:
                seen_pages.add(pu)
    for b in blocks:
        if not isinstance(b, dict):
            continue
        pu = None
        if isinstance(b.get("sources"), dict):
            pu = b["sources"].get("page_url")
        if isinstance(pu, str) and pu and pu in seen_pages:
            continue
        if isinstance(pu, str) and pu:
            seen_pages.add(pu)
        cur.append(b)
    target["html_descriptions"] = cur


def _finalize_manuscript_sources(m: dict[str, Any]) -> None:
    """
    Build manuscript.description_sources: deduped {type, url} entries from
    source_url, sources[], iiif_descriptions[].sources, html_descriptions[].sources,
    and top-level manifest URLs.
    """
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, u: str | None) -> None:
        if not u or not isinstance(u, str):
            return
        u = u.strip()
        if not u:
            return
        key = (kind, u)
        if key in seen:
            return
        seen.add(key)
        entries.append({"type": kind, "url": u})

    su = m.get("source_url")
    if isinstance(su, str):
        add("scraped_page", su)
    for s in m.get("sources") or []:
        if isinstance(s, str):
            add("scraped_page", s)
    for b in m.get("iiif_descriptions") or []:
        if not isinstance(b, dict):
            continue
        src = b.get("sources")
        if isinstance(src, dict):
            add("scraped_page", src.get("page_url"))
            add("iiif_manifest", src.get("manifest_url"))
        if isinstance(b.get("manifest_url"), str):
            add("iiif_manifest", b["manifest_url"])
    for b in m.get("html_descriptions") or []:
        if not isinstance(b, dict):
            continue
        src = b.get("sources")
        if isinstance(src, dict):
            add("scraped_page", src.get("page_url"))
    for u in m.get("manifest_urls") or []:
        if isinstance(u, str):
            add("iiif_manifest", u)
    m["description_sources"] = entries


def apply_manuscript_to_manifest(
    manifest: dict,
    out_dir: Path,
    domain: str,
    page_url: str,
    image_items: list[tuple[str, str, str, dict[str, Any] | None]],
    iiif_descriptions: list[dict[str, Any]] | None = None,
    html_descriptions: list[dict[str, Any]] | None = None,
) -> None:
    """Attach ordered pages (labels + local image paths) for manuscript / IIIF reconstruction."""
    urls_map = manifest.get("urls") or {}
    if not image_items:
        if iiif_descriptions or html_descriptions:
            stub = manifest.setdefault("manuscript", {})
            _merge_iiif_description_blocks(stub, iiif_descriptions)
            _merge_html_description_snippets(stub, html_descriptions)
            if page_url:
                stub.setdefault("source_url", page_url)
            _finalize_manuscript_sources(stub)
        return
    if not urls_map:
        if iiif_descriptions or html_descriptions:
            stub = manifest.setdefault("manuscript", {})
            _merge_iiif_description_blocks(stub, iiif_descriptions)
            _merge_html_description_snippets(stub, html_descriptions)
            if page_url:
                stub.setdefault("source_url", page_url)
            _finalize_manuscript_sources(stub)
        return
    pages: list[dict[str, Any]] = []
    manifest_urls_order: list[str] = []
    for img_url, best_url, _ct, meta in image_items:
        path_str = urls_map.get(img_url)
        if not path_str:
            continue
        dest = Path(path_str)
        try:
            rel = dest.resolve().relative_to((out_dir / domain).resolve())
        except ValueError:
            rel = dest.name
        entry: dict[str, Any] = {
            "image_url": img_url,
            "fetch_url": best_url,
            "local_path": str(rel).replace("\\", "/"),
        }
        if meta:
            for k in (
                "label",
                "canvas_id",
                "page_index",
                "manifest_url",
                "object_label",
                "manifest_id",
                "discovery_order",
                "source",
            ):
                if meta.get(k) is not None:
                    entry[k] = meta[k]
            mu = meta.get("manifest_url")
            if isinstance(mu, str) and mu and mu not in manifest_urls_order:
                manifest_urls_order.append(mu)
        pages.append(entry)
    if not pages:
        if iiif_descriptions or html_descriptions:
            stub = manifest.setdefault("manuscript", {})
            _merge_iiif_description_blocks(stub, iiif_descriptions)
            _merge_html_description_snippets(stub, html_descriptions)
            if page_url:
                stub.setdefault("source_url", page_url)
            _finalize_manuscript_sources(stub)
        return
    work_label = next((p.get("object_label") for p in pages if p.get("object_label")), None)
    new_block: dict[str, Any] = {
        "source_url": page_url,
        "page_count": len(pages),
        "pages": pages,
    }
    if work_label:
        new_block["work_label"] = work_label
    if manifest_urls_order:
        new_block["manifest_urls"] = manifest_urls_order
    if iiif_descriptions:
        _merge_iiif_description_blocks(new_block, iiif_descriptions)
    if html_descriptions:
        _merge_html_description_snippets(new_block, html_descriptions)
    _finalize_manuscript_sources(new_block)

    existing = manifest.get("manuscript")
    if not existing:
        manifest["manuscript"] = new_block
        return

    def _sources_list(m: dict[str, Any]) -> list[str]:
        su = m.get("sources")
        if isinstance(su, list):
            return [str(s) for s in su if s]
        one = m.get("source_url")
        if isinstance(one, str) and one:
            return [one]
        return []

    sources = _sources_list(existing)
    if page_url and page_url not in sources:
        sources.append(page_url)

    prev_pages = existing.get("pages") if isinstance(existing.get("pages"), list) else []
    merged_pages = prev_pages + pages

    merged: dict[str, Any] = {
        "sources": sources,
        "page_count": len(merged_pages),
        "pages": merged_pages,
    }
    wl_prev = existing.get("work_label")
    merged["work_label"] = work_label or wl_prev
    mu_prev = existing.get("manifest_urls") if isinstance(existing.get("manifest_urls"), list) else []
    mu_joined = list(dict.fromkeys([*(mu_prev or []), *manifest_urls_order]))
    if mu_joined:
        merged["manifest_urls"] = mu_joined
    prev_desc = existing.get("iiif_descriptions") if isinstance(existing.get("iiif_descriptions"), list) else []
    new_desc = new_block.get("iiif_descriptions") if isinstance(new_block.get("iiif_descriptions"), list) else []
    _merge_iiif_description_blocks(merged, prev_desc)
    _merge_iiif_description_blocks(merged, new_desc)
    prev_h = existing.get("html_descriptions") if isinstance(existing.get("html_descriptions"), list) else []
    new_h = new_block.get("html_descriptions") if isinstance(new_block.get("html_descriptions"), list) else []
    _merge_html_description_snippets(merged, prev_h)
    _merge_html_description_snippets(merged, new_h)
    _finalize_manuscript_sources(merged)
    manifest["manuscript"] = merged


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
    discovery_context: DiscoveryContext | None = None,
    preserve_text_html: bool = False,
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
    _maybe_collect_html_description(soup, url, discovery_context)

    page_links = find_page_links(soup, url, same_domain or urlparse(url).netloc)

    pdf_urls: list[str] = []
    if "pdf" in want:
        for u in find_pdf_urls(soup, url):
            if limit_pdfs is not None and len(pdf_urls) >= limit_pdfs:
                break
            pdf_urls.append(u)

    image_items: list[tuple[str, str, str, dict[str, Any] | None]] = []
    if "images" in want:
        # Manifest URLs return JSON; use httpx (not browser) so we get raw JSON
        def fetch_manifest(u: str) -> bytes:
            if use_browser:
                with Fetcher(use_browser=False) as f:
                    return f.fetch_html(u, delay=0)[0]
            return fetcher.fetch_html(u, delay=delay)[0]
        discovered = collect_image_entries(
            soup, url, html_str,
            fetch_manifest=fetch_manifest,
            limit=limit_images,
            context=discovery_context,
        )

        need_size_filter = min_image_size is not None or max_image_size is not None
        seen_best_urls: set[str] = set()

        if not need_size_filter:
            for u, page_meta in discovered:
                best = get_best_image_url(u, None, try_high_res=True)
                if best in seen_best_urls:
                    continue
                seen_best_urls.add(best)
                image_items.append((u, best, "image", page_meta))
        else:
            effective_head_workers = 1 if use_browser else head_workers
            img_urls = [t[0] for t in discovered]
            meta_by_url = {t[0]: t[1] for t in discovered}
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
                if best_url in seen_best_urls:
                    continue
                if ct and ct.strip().lower().startswith("image/gif"):
                    continue
                if content_length is not None:
                    if min_image_size is not None and content_length < min_image_size:
                        continue
                    if max_image_size is not None and content_length > max_image_size:
                        continue
                seen_best_urls.add(best_url)
                image_items.append((img_url, best_url, ct or "image", meta_by_url.get(img_url)))

    text: tuple[str, str] | None = None
    text_html: tuple[str, str] | None = None
    if "text" in want:
        extracted = extract_text(soup, html_str)
        if extracted.strip():
            text = (url, extracted)
        if preserve_text_html:
            html_body = extract_text_html(soup, html_str)
            if html_body.strip():
                text_html = (url, html_body)

    return MapResult(
        page_url=url,
        page_links=page_links,
        pdf_urls=pdf_urls,
        image_items=image_items,
        text=text,
        text_html=text_html,
        iiif_descriptions=list(discovery_context.iiif_descriptions) if discovery_context else [],
        html_descriptions=list(discovery_context.html_descriptions) if discovery_context else [],
    )


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
    failed_list: list | None = None,
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

    if result.text_html:
        turl, html = result.text_html
        th_map = manifest.setdefault("text_html", {})
        if _exists(turl, "text_html"):
            th_map[turl] = str(path_for_text_html_canonical(out_dir, domain, turl))
        elif turl not in th_map:
            dest_h = path_for_text_html(out_dir, domain, turl)
            write_text(dest_h, html)
            th_map[turl] = str(dest_h)
            if progress_callback:
                progress_callback("text")
            print(f"  Text HTML: {dest_h}", file=sys.stderr)

    work: list[tuple[str, str, str | None, dict[str, Any] | None]] = []
    for u in result.pdf_urls:
        if u not in urls_map:
            work.append((u, u, "application/pdf", None))
    for img_url, best_url, ct, meta in result.image_items:
        if img_url not in urls_map:
            work.append((img_url, best_url, ct, meta))

    n_pdf = sum(1 for _, _, ct, _ in work if ct == "application/pdf")
    n_img = len(work) - n_pdf
    total_assets = len(work)
    if total_assets > 0:
        parts = []
        if result.text:
            parts.append("text")
        if result.text_html:
            parts.append("text HTML")
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

    def _download_one(item: tuple[str, str, str | None, dict[str, Any] | None], stagger_delay: float = 0) -> bool:
        if stagger_delay > 0:
            time.sleep(stagger_delay)
        url, best_url, ct, _meta = item
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
                    if failed_list is not None:
                        failed_list.append((best_url, dest, ct or "image", url))
                    return False
            if failed_list is not None:
                failed_list.append((best_url, dest, ct or "image", url))
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
            url, best_url, ct, _ = item
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
                url, best_url, ct, _ = item
                ok = fut.result()
                if ok and progress_callback:
                    progress_callback("asset")
                with done_lock:
                    done_count[0] += 1
                print(_progress_msg(ct, ok, url, best_url), file=sys.stderr)

    apply_manuscript_to_manifest(
        manifest,
        out_dir,
        domain,
        result.page_url or "",
        result.image_items,
        iiif_descriptions=result.iiif_descriptions or None,
        html_descriptions=result.html_descriptions or None,
    )


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
    failed_list: list | None = None,
    failed_list_lock: threading.Lock | None = None,
    discovery_context: DiscoveryContext | None = None,
    preserve_text_html: bool = False,
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
    _maybe_collect_html_description(soup, url, discovery_context)

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

        if preserve_text_html:
            html_body = extract_text_html(soup, html_str)
            if html_body.strip():
                th_map = manifest.setdefault("text_html", {})
                if path_exists_for_resource(out_dir, domain, url, "text_html"):
                    th_map[url] = str(path_for_text_html_canonical(out_dir, domain, url))
                elif url not in th_map:
                    dest_h = path_for_text_html(out_dir, domain, url)
                    write_text(dest_h, html_body)
                    th_map[url] = str(dest_h)
                    if progress_callback:
                        progress_callback("text")
                    print(f"  Text HTML: {dest_h}", file=sys.stderr)

    # Build image work list (url for urls_map key, best_url to fetch, ct, dest, manuscript_meta)
    image_work: list[tuple[str, str, str, Path, dict[str, Any] | None]] = []
    need_size_filter = min_image_size is not None or max_image_size is not None
    seen_best_urls: set[str] = set()
    if "images" in want:
        fetch_manifest = lambda u: fetcher.fetch_bytes(u, delay=delay)
        discovered = collect_image_entries(
            soup, url, html_str,
            fetch_manifest=fetch_manifest,
            limit=None,
            context=discovery_context,
        )
        for img_url, page_meta in discovered:
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
                if ct and ct.strip().lower().startswith("image/gif"):
                    continue
                if content_length is not None:
                    if min_image_size is not None and content_length < min_image_size:
                        continue
                    if max_image_size is not None and content_length > max_image_size:
                        continue
            if best_url in seen_best_urls:
                continue
            seen_best_urls.add(best_url)
            canon = path_for_image_canonical(out_dir, domain, img_url, ct)
            dest = canon if canon.exists() else path_for_image(out_dir, domain, best_url, ct)
            image_work.append((img_url, best_url, ct or "image", dest, page_meta))

    # Run PDF + image downloads (parallel when asset_workers > 1)
    # Work items: (fetch_url, dest, ct, map_key) for urls_map[map_key] = str(dest)
    asset_tasks: list[tuple[str, Path, str, str]] = []
    for pdf_url, dest in pdf_work:
        asset_tasks.append((pdf_url, dest, "application/pdf", pdf_url))
    for img_url, best_url, ct, dest, _page_meta in image_work:
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
                        if failed_list is not None:
                            failed_list.append((fetch_url, dest, ct, map_key))
                        print(f"  Image fail {map_key}: {inner_e}", file=sys.stderr)
                else:
                    if failed_list is not None:
                        failed_list.append((fetch_url, dest, ct, map_key))
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
                        lock = failed_list_lock if failed_list_lock is not None else manifest_lock
                        with lock:
                            if failed_list is not None:
                                failed_list.append((fetch_url, dest, ct, map_key))
                        print(f"  {'PDF' if ct == 'application/pdf' else 'Image'} fail {map_key}: {e}", file=sys.stderr)
                        continue
                    if result is None:
                        continue
                    _map_key, _dest, _ct, status = result
                    with manifest_lock:
                        urls_map[_map_key] = str(_dest)
                        types_map[_map_key] = _ct
                    if status.startswith("fail:") and failed_list is not None:
                        lock = failed_list_lock if failed_list_lock is not None else manifest_lock
                        with lock:
                            failed_list.append((fetch_url, dest, _ct, _map_key))
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

    image_items_summary = [(a, b, c, e) for a, b, c, d, e in image_work]
    apply_manuscript_to_manifest(
        manifest,
        out_dir,
        domain,
        url,
        image_items_summary,
        iiif_descriptions=list(discovery_context.iiif_descriptions) if discovery_context else None,
        html_descriptions=list(discovery_context.html_descriptions) if discovery_context else None,
    )
    save_manifest(mf_path, manifest)

    if not collect_links:
        return []
    domain_filter = urlparse(url).netloc if same_domain_for_links is ... else same_domain_for_links
    return find_page_links(soup, url, domain_filter)


# Failed asset item: (fetch_url, dest, content_type, map_key)
FailedAssetItem = tuple[str, Path, str, str]


def retry_failed_assets(
    failed_list: list[FailedAssetItem],
    fetcher: Fetcher,
    delay: float,
    manifest: dict,
    mf_path: Path,
) -> tuple[int, list[FailedAssetItem]]:
    """
    Retry downloading failed assets sequentially. Updates manifest on success.
    Returns (succeeded_count, still_failed_list).
    """
    urls_map = manifest.setdefault("urls", {})
    types_map = manifest.setdefault("types", {})
    still_failed: list[FailedAssetItem] = []
    succeeded = 0
    for fetch_url, dest, ct, map_key in failed_list:
        try:
            fetcher.fetch_binary(fetch_url, dest, delay=delay)
            urls_map[map_key] = str(dest)
            types_map[map_key] = ct
            succeeded += 1
        except Exception:
            still_failed.append((fetch_url, dest, ct, map_key))
    return (succeeded, still_failed)


def _group_failed_by_domain(
    failed_list: list[FailedAssetItem],
    out_dir: Path,
) -> dict[str, list[FailedAssetItem]]:
    """Group failed items by domain (derived from dest path: out_dir/domain/...)."""
    by_domain: dict[str, list[FailedAssetItem]] = {}
    for item in failed_list:
        fetch_url, dest, ct, map_key = item
        try:
            rel = dest.relative_to(out_dir)
        except ValueError:
            continue
        domain = rel.parts[0] if rel.parts else ""
        by_domain.setdefault(domain, []).append(item)
    return by_domain


def _run_retry_pass(
    failed_list: list[FailedAssetItem],
    out_dir: Path,
    domain: str,
    delay: float,
    retry_timeout: float,
    use_browser: bool,
    flaresolverr_url: str | None,
    headed: bool = False,
    human_bypass: bool = False,
    *,
    domain_in_msg: bool = False,
) -> None:
    """Retry failed assets for a single domain; save manifest and write errata if any still fail."""
    if not failed_list:
        return
    mf_path = manifest_path(out_dir, domain)
    manifest = load_manifest(mf_path)
    with Fetcher(
        timeout=retry_timeout,
        use_browser=use_browser,
        flaresolverr_url=flaresolverr_url,
        headed=headed,
        human_bypass=human_bypass,
    ) as retry_fetcher:
        succ, still = retry_failed_assets(failed_list, retry_fetcher, delay, manifest, mf_path)
        save_manifest(mf_path, manifest)
        if succ:
            msg = f"  Retried {domain}: {succ} succeeded" if domain_in_msg else f"  Retried: {succ} succeeded"
            print(msg, file=sys.stderr)
        if still:
            _write_failed_urls(out_dir, domain, still)


def _write_failed_urls(out_dir: Path, domain: str, still_failed: list[FailedAssetItem]) -> None:
    """Write still-failed URLs to failed_urls.txt and destination file names to errata (same folder)."""
    if not still_failed:
        return
    folder = out_dir / domain
    folder.mkdir(parents=True, exist_ok=True)
    urls_path = folder / "failed_urls.txt"
    errata_path = folder / "errata"
    with open(urls_path, "w", encoding="utf-8") as uf, open(errata_path, "w", encoding="utf-8") as ef:
        for _fetch_url, dest, _ct, map_key in still_failed:
            uf.write(map_key + "\n")
            ef.write(dest.name + "\n")
    print(f"  Wrote {len(still_failed)} still-failed URL(s) to {urls_path}, file names to {errata_path}", file=sys.stderr)


def run_retry_from_file(
    urls_file: Path,
    out_dir: Path,
    delay: float,
    retry_timeout: float,
    use_browser: bool = False,
    flaresolverr_url: str | None = None,
    headed: bool = False,
    human_bypass: bool = False,
) -> None:
    """
    Retry downloading URLs from a file (e.g. failed_urls.txt or errata).
    File format: one URL per line. Lines starting with # are ignored.
    """
    urls: list[str] = []
    try:
        text = urls_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and (line.startswith("http://") or line.startswith("https://")):
                urls.append(line)
    except OSError as e:
        print(f"Error reading {urls_file}: {e}", file=sys.stderr)
        return
    if not urls:
        print(f"No URLs found in {urls_file}", file=sys.stderr)
        return
    failed_list: list[FailedAssetItem] = []
    for url in urls:
        domain = sanitize_domain(url)
        dest = path_for_image(out_dir, domain, url, "image")
        failed_list.append((url, dest, "image", url))
    by_domain = _group_failed_by_domain(failed_list, out_dir)
    print(f"Retrying {len(urls)} URL(s) from {urls_file}...", file=sys.stderr)
    for dom, items in by_domain.items():
        _run_retry_pass(
            items, out_dir, dom, delay, retry_timeout,
            use_browser, flaresolverr_url, headed, human_bypass,
            domain_in_msg=True,
        )


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
    retry_failed = getattr(args, "retry_failed", True)
    retry_timeout = getattr(args, "retry_timeout", 90)
    # Higher timeout for browser mode (large IIIF images often need 90s+)
    fetcher_timeout = retry_timeout if args.js else DEFAULT_TIMEOUT
    with Fetcher(
        timeout=fetcher_timeout,
        use_browser=args.js,
        flaresolverr_url=getattr(args, "flaresolverr_url", None),
        headed=getattr(args, "headed", False),
        human_bypass=getattr(args, "human_bypass", False),
    ) as fetcher:
        if args.crawl:
            start_domain = urlparse(args.url).netloc
            same_domain_only = args.same_domain_only
            retried_cross_domain = False
            failed_list: list[FailedAssetItem] = [] if retry_failed else []

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
                        discovery_ctx = DiscoveryContext(
                            url=url,
                            expected_images=getattr(args, "expected_images", None),
                            source_hint=getattr(args, "source", None),
                            manuscript_mode=getattr(args, "manuscript", False),
                        )
                        links = scrape_page(
                            url, out_dir, args.delay, manifest, fetcher,
                            limit, limit, collect_links=True, types=types_set,
                            progress_callback=None,
                            min_image_size=min_image_size,
                            max_image_size=max_image_size,
                            same_domain_for_links=link_filter,
                            failed_list=failed_list if retry_failed else None,
                            discovery_context=discovery_ctx,
                            preserve_text_html=getattr(args, "text_html", False),
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
            if retry_failed and failed_list:
                print(f"  Retrying {len(failed_list)} failed asset(s)...", file=sys.stderr)
                by_domain = _group_failed_by_domain(failed_list, out_dir)
                for dom, items in by_domain.items():
                    _run_retry_pass(
                        items, out_dir, dom, args.delay, retry_timeout,
                        args.js, getattr(args, "flaresolverr_url", None),
                        getattr(args, "headed", False), getattr(args, "human_bypass", False),
                        domain_in_msg=True,
                    )
        else:
            if not getattr(args, "no_robots", False) and not can_fetch(args.url):
                print("robots.txt disallows this URL.", file=sys.stderr)
                sys.exit(1)
            domain = sanitize_domain(args.url)
            manifest = load_manifest(manifest_path(out_dir, domain))
            failed_list_sp: list[FailedAssetItem] = [] if retry_failed else []
            max_iterations = max(1, getattr(args, "max_iterations", 3))
            had_403 = False
            for iteration in range(max_iterations):
                delay_i = args.delay * (ITERATION_DELAY_FACTOR ** iteration)
                use_browser = args.js or (iteration > 0 and had_403)
                # Higher base timeout when using browser (large IIIF images need 90s+)
                base_timeout = retry_timeout if use_browser else DEFAULT_TIMEOUT
                timeout_i = min(
                    base_timeout * (ITERATION_TIMEOUT_FACTOR ** iteration),
                    MAX_TIMEOUT,
                )
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
                            discovery_ctx = DiscoveryContext(
                                url=args.url,
                                expected_images=getattr(args, "expected_images", None),
                                source_hint=getattr(args, "source", None),
                                manuscript_mode=getattr(args, "manuscript", False),
                            )
                            map_result = map_page(
                                args.url,
                                iter_fetcher,
                                want,
                                limit, limit,
                                min_image_size, max_image_size,
                                delay_i,
                                head_workers=min(SAFE_HEAD_WORKERS, workers),
                                use_browser=use_browser,
                                discovery_context=discovery_ctx,
                                preserve_text_html=getattr(args, "text_html", False),
                            )
                            fetcher_ctx = (
                                (lambda f: lambda: nullcontext(f))(iter_fetcher)
                                if use_browser
                                else (lambda: Fetcher(timeout=60, use_browser=False, flaresolverr_url=getattr(args, "flaresolverr_url", None)))
                            )
                            n_pdf, n_img = len(map_result.pdf_urls), len(map_result.image_items)
                            if n_pdf or n_img or map_result.text or map_result.text_html:
                                parts = []
                                if map_result.text:
                                    parts.append("text")
                                if map_result.text_html:
                                    parts.append("text (HTML)")
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
                                failed_list=failed_list_sp if retry_failed else None,
                            )
                            if retry_failed and failed_list_sp:
                                print(f"  Retrying {len(failed_list_sp)} failed asset(s)...", file=sys.stderr)
                                _run_retry_pass(
                                    failed_list_sp, out_dir, domain, delay_i, retry_timeout,
                                    use_browser, getattr(args, "flaresolverr_url", None),
                                    getattr(args, "headed", False), getattr(args, "human_bypass", False),
                                )
                            save_manifest(manifest_path(out_dir, domain), manifest)
                        else:
                            print("  → Fetching and extracting page...", file=sys.stderr)
                            discovery_ctx = DiscoveryContext(
                                url=args.url,
                                expected_images=getattr(args, "expected_images", None),
                                source_hint=getattr(args, "source", None),
                                manuscript_mode=getattr(args, "manuscript", False),
                            )
                            scrape_page(
                                args.url, out_dir, delay_i, manifest, iter_fetcher,
                                limit, limit, collect_links=False, types=types_set,
                                progress_callback=progress_cb,
                                min_image_size=min_image_size,
                                max_image_size=max_image_size,
                                failed_list=failed_list_sp if retry_failed else None,
                                discovery_context=discovery_ctx,
                                preserve_text_html=getattr(args, "text_html", False),
                            )
                            if retry_failed and failed_list_sp:
                                print(f"  Retrying {len(failed_list_sp)} failed asset(s)...", file=sys.stderr)
                                _run_retry_pass(
                                    failed_list_sp, out_dir, domain, delay_i, retry_timeout,
                                    use_browser, getattr(args, "flaresolverr_url", None),
                                    getattr(args, "headed", False), getattr(args, "human_bypass", False),
                                )
                            save_manifest(manifest_path(out_dir, domain), manifest)
                except Exception as e:
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
    retry_failed: bool = True,
    retry_timeout: float = 90,
    no_robots: bool = False,
    headed: bool = False,
    human_bypass: bool = False,
    expected_images: int | None = None,
    source_hint: str | None = None,
    manuscript_mode: bool = False,
    preserve_text_html: bool = False,
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
        failed_list: list[FailedAssetItem] = [] if retry_failed else []
        failed_list_lock = threading.Lock() if retry_failed else None
        pbar = tqdm(desc="Crawl", unit=" page", file=sys.stderr) if (tqdm and use_progress) else None

        def process_one(url: str, depth: int, fetcher: Fetcher) -> list[str]:
            if not no_robots and not can_fetch(url):
                return []
            domain = sanitize_domain(url)
            with manifest_lock:
                manifest = load_manifest(manifest_path(out_dir, domain))
                try:
                    discovery_ctx = DiscoveryContext(
                        url=url,
                        expected_images=expected_images,
                        source_hint=source_hint,
                        manuscript_mode=manuscript_mode,
                    )
                    links = scrape_page(
                        url, out_dir, delay, manifest, fetcher,
                        limit, limit, collect_links=True, types=types_set,
                        progress_callback=None,
                        min_image_size=min_image_size,
                        max_image_size=max_image_size,
                        same_domain_for_links=link_filter,
                        asset_workers=min(SAFE_ASSET_WORKERS, workers),
                        failed_list=failed_list if retry_failed else None,
                        failed_list_lock=failed_list_lock,
                        discovery_context=discovery_ctx,
                        preserve_text_html=preserve_text_html,
                    )
                except Exception as e:
                    print(f"Error {url}: {e}", file=sys.stderr)
                    return []
                save_manifest(manifest_path(out_dir, domain), manifest)
            return links

        def worker() -> None:
            nonlocal pending
            # Higher timeout for browser mode (large IIIF images often need 90s+)
            worker_timeout = retry_timeout if use_browser else DEFAULT_TIMEOUT
            fetcher = Fetcher(
                timeout=worker_timeout,
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
        if retry_failed and failed_list:
            print(f"  Retrying {len(failed_list)} failed asset(s)...", file=sys.stderr)
            by_domain = _group_failed_by_domain(failed_list, out_dir)
            for dom, items in by_domain.items():
                _run_retry_pass(
                    items, out_dir, dom, delay, retry_timeout,
                    use_browser, flaresolverr_url, headed, human_bypass,
                    domain_in_msg=True,
                )
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
