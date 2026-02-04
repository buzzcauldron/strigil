"""Basic scraper: PDFs, text, and images from websites."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("basic-scraper")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
