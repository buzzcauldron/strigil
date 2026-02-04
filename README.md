# basic-scraper

Basic scraper: PDFs, text, and images from websites at high quality, stored locally.

## Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR**: Incompatible CLI or API changes (e.g. rename/remove of `--url`, breaking changes in Python API).
- **MINOR**: New features in a backward-compatible way (e.g. new `--crawl` behavior, new extractors).
- **PATCH**: Backward-compatible bug fixes and small improvements.

For the **0.y.z** range, the public API is treated as unstable: MINOR may introduce breaking changes if needed. Once we commit to stability, we move to 1.0.0 and follow strict semver. See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT. See [LICENSE](LICENSE).

## Install and run

From the project directory:

```bash
pip install -e .
```

This installs the package in editable mode and registers the `scrape` and `scrape-gui` console scripts. You can then run:

```bash
scrape --url https://example.com/page [--out-dir output] [--delay 1] [--crawl] [--max-depth 2] [--same-domain-only]
```

Or open the simple GUI:

```bash
scrape-gui
```

Optional: install Playwright for JS-heavy pages:

```bash
pip install -e ".[js]"
playwright install
```

### If dependencies are missing

When you run `scrape` or `scrape-gui`, the app checks that required dependencies (httpx, beautifulsoup4, lxml, readability-lxml) are installed. If any are missing, it prints install instructions and exits:

- **From PyPI:** `pip install basic-scraper`
- **From source:** `pip install -e .` (in the project directory)

An optional one-line hint is shown if Playwright is not installed (for JS rendering).

## Building a standalone bundle

To build a standalone folder with the CLI and GUI (no Python required on the target machine):

```bash
pip install -e ".[bundle]"
pyinstaller basic-scraper.spec
```

Output is in `dist/basic-scraper/`: run `scrape` or `scrape-gui` from that folder. The GUI uses the bundled `scrape` executable in the same directory when you click Scrape.
