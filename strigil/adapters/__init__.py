"""Archive adapters: extensible schema for scraping images from archive websites.

When adding a new adapter: register in ALL_ADAPTERS and ADAPTER_BY_SOURCE (for --source hint).
"""

from strigil.adapters.base import ArchiveAdapter
from strigil.adapters.internet_archive import InternetArchiveAdapter
from strigil.adapters.wellcome import WellcomeAdapter

# Shared instances (adapters are stateless)
_internet_archive = InternetArchiveAdapter()
_wellcome = WellcomeAdapter()

ALL_ADAPTERS: list[ArchiveAdapter] = [_internet_archive, _wellcome]

# Map source hint (--source) to adapter for forced extraction
ADAPTER_BY_SOURCE: dict[str, ArchiveAdapter] = {
    "wellcome": _wellcome,
    "archive_org": _internet_archive,
    "internet_archive": _internet_archive,
}
