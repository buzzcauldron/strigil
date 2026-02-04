# basic-scraper

Basic scraper: PDFs, text, and images from websites at high quality, stored locally.

**Author:** Seth Strickland · **License:** [MIT](LICENSE)

## Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR**: Incompatible CLI or API changes (e.g. rename/remove of `--url`, breaking changes in Python API).
- **MINOR**: New features in a backward-compatible way (e.g. new `--crawl` behavior, new extractors).
- **PATCH**: Backward-compatible bug fixes and small improvements.

For the **0.y.z** range, the public API is treated as unstable: MINOR may introduce breaking changes if needed. Once we commit to stability, we move to 1.0.0 and follow strict semver. See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT License. Copyright (c) 2025 Seth Strickland. See [LICENSE](LICENSE).

## Install and run

From the project directory:

```bash
pip install -e .
```

This installs the package in editable mode and registers the `scrape` and `scrape-gui` console scripts. You can then run:

```bash
scrape --url https://example.com/page [--out-dir output] [--delay 1] [--crawl] [--max-depth 2] [--same-domain-only]
```

Filter images by file size (uses HEAD `Content-Length`): `--min-image-size 50k` and/or `--max-image-size 5m` (suffixes `k`/`m` for KB/MB).

Or open the simple GUI:

```bash
scrape-gui
```

Optional: install Playwright for JS-heavy pages:

```bash
pip install -e ".[js]"
playwright install
```

Optional: install tqdm for a progress bar (per-page in crawl, per-asset on single page):

```bash
pip install -e ".[progress]"
```

Use `--no-progress` to disable the bar (e.g. in scripts).

### If dependencies are missing

When you run `scrape` or `scrape-gui`, the app checks that required dependencies (httpx, beautifulsoup4, lxml) are installed. If any are missing, it prints install instructions and exits.

- **From PyPI:** `pip install basic-scraper`
- **From source:** `pip install -e .` (in the project directory)
- **Auto-install:** set the env var and re-run; the app will run `pip install` for required deps and exit—run the command again after that. On subsequent runs, if the var is set, missing optional deps (Playwright, tqdm, readability-lxml) are installed automatically and the app continues:
  ```bash
  BASIC_SCRAPER_AUTO_INSTALL_DEPS=1 scrape --url https://example.com
  ```

An optional one-line hint is shown if Playwright is not installed (for JS rendering).

### Workers and hardware autodetect

For **crawl** mode, the scraper auto-detects CPU count and caps parallel workers (default: up to 4) for effective scraping while staying light. Override with `--workers N`:

```bash
scrape --url https://example.com --crawl --workers 2
```

## Building a standalone bundle

To build a standalone folder with the CLI and GUI (no Python required on the target machine):

```bash
pip install -e ".[bundle]"
pyinstaller basic-scraper.spec
```

Output is in `dist/basic-scraper/`: run `scrape` or `scrape-gui` from that folder. The GUI uses the bundled `scrape` executable in the same directory when you click Scrape.

## Docker

Light image (CLI only, no GUI):

```bash
docker build -t basic-scraper .
docker run --rm -v "$(pwd)/output:/scrape/output" basic-scraper --url https://example.com --out-dir /scrape/output
```

Override the default URL and options by passing args after the image name.

## CI: build all OS and Docker

On push/PR to `main` or `master`, GitHub Actions:

- Builds PyInstaller bundles on **Ubuntu, macOS, and Windows** and uploads artifacts (`basic-scraper-<os>`).
- Builds the **Docker** image and runs a quick smoke test.

See [.github/workflows/build.yml](.github/workflows/build.yml).
