"""Wrapper around urllib.robotparser for crawl mode."""

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "Strigil/1.0 (+https://github.com/sethstrickland/strigil; PDF/text/image scraper)"

# Cache: RobotFileParser on success, None when fetch failed (treat as allow-all)
_robots_cache: dict[tuple[str, str], RobotFileParser | None] = {}


def _get_parser(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    key = (parsed.scheme or "https", parsed.netloc or "")
    if key not in _robots_cache:
        base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        robots_url = urljoin(base, "/robots.txt")
        rp = RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            _robots_cache[key] = rp
        except Exception:
            _robots_cache[key] = None  # allow all when robots.txt unreachable
    return _robots_cache[key]


def can_fetch(url: str, user_agent: str = USER_AGENT) -> bool:
    """Check if robots.txt allows fetching the URL."""
    try:
        parser = _get_parser(url)
        if parser is None:
            return True
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True
