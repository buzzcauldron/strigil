"""Archive adapters: extensible schema for scraping images from archive websites.

When adding a new adapter: register in ALL_ADAPTERS and ADAPTER_BY_SOURCE (for --source hint).
"""

from strigil.adapters.base import ArchiveAdapter
from strigil.adapters.internet_archive import InternetArchiveAdapter
from strigil.adapters.wellcome import WellcomeAdapter

ALL_ADAPTERS: list[ArchiveAdapter] = [
    InternetArchiveAdapter(),
    WellcomeAdapter(),
]

# Map source hint (--source) to adapter for forced extraction
ADAPTER_BY_SOURCE: dict[str, ArchiveAdapter] = {
    "wellcome": WellcomeAdapter(),
    "archive_org": InternetArchiveAdapter(),
    "internet_archive": InternetArchiveAdapter(),
}
