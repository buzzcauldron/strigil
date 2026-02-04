"""Light hardware autodetection for effective scraping (workers, concurrency)."""

import os

# Cap workers to stay light and polite; scale with CPU for crawl.
MAX_WORKERS = 4
MIN_WORKERS = 1

# Cap parallel asset downloads; balance speed vs polite crawling.
SAFE_ASSET_WORKERS = 5
SAFE_HEAD_WORKERS = 4


def default_workers() -> int:
    """Suggested number of workers from CPU count (for crawl / future parallel fetch)."""
    n = os.cpu_count()
    if n is None or n < 1:
        return MIN_WORKERS
    return max(MIN_WORKERS, min(n, MAX_WORKERS))
