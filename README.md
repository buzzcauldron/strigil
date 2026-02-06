# basic-scraper

Basic scraper: PDFs, text, and images from websites at high quality, stored locally.

**Author:** Seth Strickland · **License:** [MIT](LICENSE)

## License

MIT License. Copyright (c) 2025 Seth Strickland. See [LICENSE](LICENSE).

## Install and run

From the project directory:

```bash
pip install -e .
```

This installs the package in editable mode and registers the `scrape` and `scrape-gui` console scripts. You can then run:

```bash
scrape --url https://example.com/page [URL2 ...] [--out-dir output] [--delay 1] [--crawl] [--max-depth 2] [--same-domain-only]
```

Filter images by file size (uses HEAD `Content-Length`): `--min-image-size 50k` and/or `--max-image-size 5m` (suffixes `k`/`m` for KB/MB). Use a low or zero minimum to capture thumbnails; a high minimum (e.g. `1m`) skips smaller images.

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

Use `--keep-awake` to prevent system/display sleep during long scrapes. On Linux this requires the **systemd-inhibit** binary (usually provided by your distro's systemd package, e.g. `sudo apt install systemd`). If you use keep-awake on Linux and it's not installed, the app prints an optional install hint.

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

For **crawl** mode, the scraper auto-detects CPU count and caps parallel workers (default: up to 12) for faster scraping. Override with `--workers N`. To see detected hardware (CPU, memory if available, suggested workers), run `scrape --hardware`.

**Faster crawl and scrape:** Use `--aggressiveness aggressive` (or `--workers 12 --delay 0.15`) for maximum speed. More workers = more pages in parallel; lower delay = less wait between requests. Per-page asset downloads and image HEADs also run with higher parallelism (up to 8 assets, 6 HEADs).

**Aggressiveness (auto from hardware and power):** Use `--aggressiveness auto` (default) to let the scraper pick conservative, balanced, or aggressive. On **AC power** with strong hardware it suggests **aggressive**; on **battery** it throttles by **battery %**—below 20% always conservative, 50%+ may allow balanced if hardware allows. Run `scrape --hardware` to see power and battery % and the suggested preset.

```bash
scrape --url https://example.com --crawl --workers 6 --delay 0.3
scrape --url https://example.com --crawl --aggressiveness aggressive
```

### Iterations and auto timeout (single-page)

On 403 or slow responses, the scraper retries automatically:

- **Iterations:** Single-page runs retry up to `--max-iterations` (default 3). Each iteration uses a longer delay and timeout; if the first attempt gets 403, the next iteration automatically uses the browser (`--js`) when Playwright is installed.
- **Auto timeout:** Per-request timeout scales with each retry (30s → 60s → 120s, cap 120s). Suggested default is 120s max; override base with a custom timeout in code if needed.

```bash
scrape --url https://strict.site/page --max-iterations 5
```

## Building a standalone bundle

To build a standalone folder with the CLI and GUI (no Python required on the target machine):

```bash
pip install -e ".[bundle]"
pyinstaller basic-scraper.spec
```

Output is in `dist/basic-scraper/`: run `scrape` or `scrape-gui` from that folder. The GUI uses the bundled `scrape` executable in the same directory when you click Scrape.

### Install packages (Mac, Windows, Linux)

Build an install package for the current platform (folder + archive):

| Platform | Script | Output |
|----------|--------|--------|
| **macOS** | `./scripts/build_mac.sh` | `dist/basic-scraper-mac.zip` |
| **Linux** | `./scripts/build_linux.sh` | `dist/basic-scraper-linux.tar.gz` |
| **Windows** | `scripts\build_windows.bat` | `dist\basic-scraper-win.zip` |

Each script runs `pip install -e ".[bundle]"`, `pyinstaller basic-scraper.spec`, then creates the archive. Unzip (or unpack the tarball) and run `scrape` or `scrape-gui` from the `basic-scraper` folder.

## Docker

Light image (CLI only, no GUI):

```bash
docker build -t basic-scraper .
docker run --rm -v "$(pwd)/output:/scrape/output" basic-scraper --url https://example.com --out-dir /scrape/output
```

Override the default URL and options by passing args after the image name.

## CI: build all OS and Docker

On push/PR to `main` or `master`, GitHub Actions:

- Builds PyInstaller bundles on **Ubuntu, macOS, and Windows** and uploads:
  - **basic-scraper-&lt;os&gt;** – the `dist/basic-scraper/` folder
  - **basic-scraper-&lt;os&gt;-install** – install package: `basic-scraper-win.zip`, `basic-scraper-mac.zip`, or `basic-scraper-linux.tar.gz`
- Builds the **Docker** image and runs a quick smoke test.

See [.github/workflows/build.yml](.github/workflows/build.yml).
