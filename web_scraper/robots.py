"""Wrapper around urllib.robotparser for crawl mode."""

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "WebScraper"


def can_fetch(url: str, user_agent: str = USER_AGENT) -> bool:
    """
    Check if robots.txt allows fetching the URL.
    Returns True if no robots.txt exists or if fetching is allowed.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(base, "/robots.txt")
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True
