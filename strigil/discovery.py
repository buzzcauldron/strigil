"""
Image URL discovery: schema detection pipeline + extraction.

Uses strigil.schema to detect image storage schema (CONTENTdm, NYPL, IIIF,
generic HTML) and run the appropriate extraction strategy for full-resolution images.
"""

from typing import Callable

from bs4 import BeautifulSoup

from strigil.schema import collect_image_urls as _collect_image_urls


def collect_image_urls(
    soup: BeautifulSoup,
    url: str,
    html_str: str,
    *,
    fetch_manifest: Callable[[str], bytes] | None = None,
    limit: int | None = None,
) -> list[str]:
    """
    Detect image storage schema and collect image URLs using the proper strategy.
    Delegates to the schema detection pipeline.
    """
    return _collect_image_urls(
        soup,
        url,
        html_str,
        fetch_manifest=fetch_manifest,
        limit=limit,
    )
