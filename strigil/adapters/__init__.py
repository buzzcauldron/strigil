"""Archive adapters: extensible schema for scraping images from archive websites.

When adding a new adapter: register in ALL_ADAPTERS and ADAPTER_BY_SOURCE (for --source hint).
"""

from strigil.adapters.base import ArchiveAdapter
from strigil.adapters.eebo import EeboAdapter
from strigil.adapters.ecco import EccoAdapter
from strigil.adapters.hathitrust import HathiTrustAdapter
from strigil.adapters.internet_archive import InternetArchiveAdapter
from strigil.adapters.wellcome import WellcomeAdapter

# Shared instances (adapters are stateless)
_internet_archive = InternetArchiveAdapter()
_wellcome = WellcomeAdapter()
_hathitrust = HathiTrustAdapter()
_eebo = EeboAdapter()
_ecco = EccoAdapter()

ALL_ADAPTERS: list[ArchiveAdapter] = [
    _hathitrust,
    _internet_archive,
    _wellcome,
    _eebo,
    _ecco,
]

# Map source hint (--source) to adapter for forced extraction
ADAPTER_BY_SOURCE: dict[str, ArchiveAdapter] = {
    "wellcome": _wellcome,
    "archive_org": _internet_archive,
    "internet_archive": _internet_archive,
    "hathitrust": _hathitrust,
    "hathi": _hathitrust,
    "eebo": _eebo,
    "proquest": _eebo,
    "ecco": _ecco,
    "gale": _ecco,
}
