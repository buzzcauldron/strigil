"""CLI entry point for the scraper. Invoked as `scrape` when installed with pip install -e ."""

import argparse
import sys
import warnings

# Suppress harmless multiprocessing semaphore leak warning (e.g. from deps on exit)
warnings.filterwarnings("ignore", message=r".*leaked semaphore.*", category=UserWarning)
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from web_scraper._deps import check_required, ensure_optional, optional_hint
from web_scraper.hardware import (
    AGGRESSIVENESS_CHOICES,
    default_workers,
    detect_hardware,
    format_hardware,
    get_aggressiveness_params,
    suggest_aggressiveness,
)
from web_scraper.keep_awake import keep_awake
from web_scraper.pipeline import (
    VALID_TYPES,
    crawl_parallel,
    parse_size,
    run_done_script,
    run_single_or_sequential_crawl,
)


def main() -> None:
    check_required()
    ensure_optional()
    hint = optional_hint()
    if hint:
        print(hint, file=sys.stderr)

    parser = argparse.ArgumentParser(
        prog="scrape",
        description="Scrape PDFs, text, and images from a URL and store locally.",
    )
    parser.add_argument("--url", nargs="*", default=None, metavar="URL", help="URL(s) to scrape (one or more)")
    parser.add_argument("--hardware", action="store_true", help="Print detected hardware and suggested workers, then exit.")
    parser.add_argument("--out-dir", default="output", help="Output directory (default: output)")
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECS",
        help="Delay between requests in seconds (default from --aggressiveness)",
    )
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
    parser.add_argument(
        "--js",
        action="store_true",
        help="Fetch HTML with a real browser (Playwright). Use for JS-heavy or bot-protected sites.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        metavar="N",
        help="Max retry iterations on 403/slow (each uses longer timeout/delay; auto-escalates to browser if needed). Default 3.",
    )
    parser.add_argument(
        "--map-first",
        action="store_true",
        default=True,
        help="Map URLs first, then scrape in parallel (default). Faster for asset-heavy pages.",
    )
    parser.add_argument(
        "--no-map-first",
        action="store_false",
        dest="map_first",
        help="Disable map-first; scrape sequentially as discovered (legacy mode).",
    )
    parser.add_argument(
        "--done-script",
        type=str,
        default=None,
        metavar="CMD",
        help="Shell command to run when scrape completes. Use {out_dir} for output path.",
    )
    parser.add_argument(
        "--keep-awake",
        action="store_true",
        help="Prevent system/display sleep during the scrape (for long runs).",
    )
    parser.add_argument(
        "--aggressiveness",
        choices=AGGRESSIVENESS_CHOICES,
        default="auto",
        metavar="MODE",
        help="Scrape speed vs politeness: auto (detect from hardware), conservative, balanced, aggressive.",
    )
    args = parser.parse_args()

    if getattr(args, "hardware", False):
        print(format_hardware(), file=sys.stderr)
        sys.exit(0)

    # Apply aggressiveness preset when user did not explicitly set workers/delay
    if getattr(args, "aggressiveness", None):
        hw = detect_hardware()
        preset = suggest_aggressiveness(hw) if args.aggressiveness == "auto" else args.aggressiveness
        params = get_aggressiveness_params(args.aggressiveness, hw)
        if args.workers is None:
            args.workers = params["workers"]
        if args.delay is None:
            args.delay = params["delay"]
        if args.aggressiveness == "auto":
            print(
                f"Aggressiveness: {preset} (workers={args.workers}, delay={args.delay}s)",
                file=sys.stderr,
            )
    if args.delay is None:
        args.delay = 0.5

    if not args.url or not [u for u in args.url if u and str(u).strip()]:
        parser.error("At least one URL is required (or use --hardware to print hardware info).")

    out_dir = Path(args.out_dir)
    limit = args.limit
    types_set = set(args.types) if args.types else None
    min_image_size: int | None = None
    max_image_size: int | None = None
    for opt, val in (("--min-image-size", args.min_image_size), ("--max-image-size", args.max_image_size)):
        if not val:
            continue
        try:
            parsed = parse_size(val)
            if "min" in opt:
                min_image_size = parsed
            else:
                max_image_size = parsed
        except ValueError as e:
            parser.error(f"{opt}: {e}")
    workers = args.workers if args.workers is not None else default_workers()
    workers = max(1, min(workers, default_workers()))

    use_progress = not args.no_progress and tqdm is not None
    urls = [u.strip() for u in args.url if u and u.strip()]
    if not urls:
        print("Error: No valid URL(s) provided.", file=sys.stderr)
        sys.exit(1)

    def _run() -> None:
        for i, url in enumerate(urls):
            if len(urls) > 1:
                print(f"\n——— Site {i + 1}/{len(urls)}: {url} ———", file=sys.stderr)
            args.url = url
            if args.crawl and workers > 1:
                crawl_parallel(
                    url, out_dir, args.delay, args.max_depth,
                    args.same_domain_only, limit, types_set, workers, use_progress,
                    min_image_size, max_image_size, use_browser=args.js,
                )
            else:
                run_single_or_sequential_crawl(
                    args, out_dir, limit, types_set, workers, use_progress,
                    min_image_size, max_image_size,
                )
        if getattr(args, "done_script", None):
            run_done_script(args.done_script, out_dir)
        print("\nDone.", file=sys.stderr)

    if getattr(args, "keep_awake", False):
        with keep_awake():
            _run()
    else:
        _run()


if __name__ == "__main__":
    main()
