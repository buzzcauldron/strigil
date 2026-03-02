"""Base protocol for archive adapters."""

from __future__ import annotations

from typing import Callable, Protocol


class ArchiveAdapter(Protocol):
    """Protocol for archive-specific image extraction.

    Adapters allow new archive types to be added without modifying core schema logic.
    """

    def matches(self, url: str) -> bool:
        """Return True if this adapter handles the given URL."""
        ...

    def extract_image_urls(
        self,
        url: str,
        html: str,
        fetch: Callable[[str], bytes] | None,
    ) -> list[str]:
        """Extract full-resolution image URLs from the archive page.

        :param url: The page URL
        :param html: Raw HTML of the page (may be empty for API-only adapters)
        :param fetch: Optional fetcher for manifests/APIs (url -> bytes)
        :return: List of image URLs
        """
        ...
