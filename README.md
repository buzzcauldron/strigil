# strigil

Strigil: PDFs, text, and images from websites at high quality, stored locally.

**Repository:** [strigil](https://github.com/sethstrickland/strigil) · **Author:** Seth Strickland · **License:** [MIT](LICENSE)

## License

MIT License. Copyright (c) 2025 Seth Strickland. See [LICENSE](LICENSE).

## Install and run

From the project directory:

```bash
pip install -e .
```

This installs the package in editable mode and registers the `strigil` and `strigil-gui` console scripts. You can then run:

```bash
strigil --url https://example.com/page [URL2 ...] [--out-dir output] [--delay 1] [--crawl] [--max-depth 2] [--same-domain-only]
```

Filter images by file size (uses HEAD `Content-Length`): `--min-image-size 50k` and/or `--max-image-size 5m` (suffixes `k`/`m` for KB/MB). Use a low or zero minimum to capture thumbnails; a high minimum (e.g. `1m`) skips smaller images.

Or open the simple GUI:

```bash
strigil-gui
```

Use `--js` for JS-heavy pages (e.g. NYPL Digital Collections). Playwright and Chromium are installed automatically with the package; on first `--js` use, Chromium is downloaded if needed.

Optional: install tqdm for a progress bar (per-page in crawl, per-asset on single page):

```bash
pip install -e ".[progress]"
```

Use `--no-progress` to disable the bar (e.g. in scripts).

Use `--keep-awake` to prevent system/display sleep during long scrapes. On Linux this requires the **systemd-inhibit** binary (usually provided by your distro's systemd package, e.g. `sudo apt install systemd`). If you use keep-awake on Linux and it's not installed, the app prints an optional install hint.

### If dependencies are missing

When you run `strigil` or `strigil-gui`, the app auto-installs missing dependencies on first run. Required deps (httpx, beautifulsoup4, lxml) are installed and the app exits—run the command again. Optional deps (Playwright, tqdm, readability-lxml) are installed and the app continues. Set `STRIGIL_AUTO_INSTALL_DEPS=0` to disable auto-install.

- **From PyPI:** `pip install strigil`
- **From source:** `pip install -e .` (in the project directory)

If Playwright is not installed, an optional hint is shown for JS rendering (`--js`).

### Workers and hardware autodetect

For **crawl** mode, the scraper auto-detects CPU count and caps parallel workers (default: up to 12) for faster scraping. Override with `--workers N`. To see detected hardware (CPU, memory if available, suggested workers), run `strigil --hardware`.

**Faster crawl and scrape:** Use `--aggressiveness aggressive` (or `--workers 12 --delay 0.15`) for maximum speed. More workers = more pages in parallel; lower delay = less wait between requests. Per-page asset downloads and image HEADs also run with higher parallelism (up to 8 assets, 6 HEADs).

**Aggressiveness (auto from hardware and power):** Use `--aggressiveness auto` (default) to let the scraper pick conservative, balanced, or aggressive. On **AC power** with strong hardware it suggests **aggressive**; on **battery** it throttles by **battery %**—below 20% always conservative, 50%+ may allow balanced if hardware allows. Run `strigil --hardware` to see power and battery % and the suggested preset.

```bash
strigil --url https://example.com --crawl --workers 6 --delay 0.3
strigil --url https://example.com --crawl --aggressiveness aggressive
```

### Cloudflare and bot protection

Some sites use **Cloudflare**, **DDoS-GUARD**, or similar protection and may return 403, challenge pages, or block non-browser traffic. The built-in `--js` mode (Playwright) can help on JS-heavy or bot-detecting sites, but it does not solve Cloudflare challenges.

This scraper can fetch HTML via **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** so Cloudflare-protected pages are solved by FlareSolverr’s headless browser and the cleared HTML is used for link and image discovery.

1. Run FlareSolverr (e.g. Docker): `docker run -d -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest`
2. Enable it when scraping:
   - **CLI:** `strigil --url https://... --flaresolverr` (uses `http://localhost:8191` or `FLARESOLVERR_URL`)
   - **CLI with custom URL:** `strigil --url https://... --flaresolverr http://host:8191`
   - **Env:** set `FLARESOLVERR_URL=http://localhost:8191` and pass `--flaresolverr`
   - **GUI:** check “FlareSolverr (Cloudflare bypass)” and optionally set the FlareSolverr API URL.

When `--flaresolverr` is set, page HTML is requested through FlareSolverr; asset downloads (images, PDFs) still use the normal fetcher. With crawl, the scraper uses one worker when FlareSolverr is enabled (same as with `--js`).

- **FlareSolverr:** [https://github.com/FlareSolverr/FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) — proxy server to bypass Cloudflare and DDoS-GUARD protection (MIT license).

### Rate limits and automatic throttling

When the scraper receives **HTTP 429 (Too Many Requests)** or a **200 response whose body indicates a rate limit** (e.g. “Rate limit reached”, “too many requests”), it:

- Waits before retrying: uses the **Retry-After** header when present (seconds or HTTP-date), otherwise 30–60s.
- **Throttles subsequent requests**: a per-Fetcher delay is applied so all following requests (HTML, images, HEADs) are slowed until the backend recovers. The delay decays gradually after successful responses.

**502/503/504** (Bad Gateway, Service Unavailable, Gateway Timeout) are retried up to 6 times with a 5s base wait so flaky upstream servers (e.g. IIIF image servers) often succeed on retry.

Sites like Archive-It that return a rate-limit message in the HTML body are handled the same way: wait, retry, and throttle.

### Iterations and auto timeout (single-page)

On 403 or slow responses, the scraper retries automatically:

- **Iterations:** Single-page runs retry up to `--max-iterations` (default 3). Each iteration uses a longer delay and timeout; if the first attempt gets 403, the next iteration automatically uses the browser (`--js`) when Playwright is installed. For sites behind Cloudflare or similar protection, see [Cloudflare and bot protection](#cloudflare-and-bot-protection) above.
- **Auto timeout:** Per-request timeout scales with each retry (30s → 60s → 120s, cap 120s). Suggested default is 120s max; override base with a custom timeout in code if needed.

```bash
strigil --url https://strict.site/page --max-iterations 5
```

## Building a standalone bundle

To build a standalone folder with the CLI and GUI (no Python required on the target machine):

```bash
pip install -e ".[bundle]"
pyinstaller strigil.spec
```

Output is in `dist/strigil/`: run `strigil` or `strigil-gui` from that folder. The GUI uses the bundled `strigil` executable in the same directory when you click Scrape.

### Install packages (Mac, Windows, Linux)

Build an install package for the current platform (folder + archive):

| Platform | Script | Output |
|----------|--------|--------|
| **macOS** | `./scripts/build_mac.sh` | `dist/strigil-mac.zip` |
| **Linux** | `./scripts/build_linux.sh` | `dist/strigil-linux.tar.gz` |
| **Windows** | `scripts\build_windows.bat` | `dist\strigil-win.zip` |

Each script runs `pip install -e ".[bundle]"`, `pyinstaller strigil.spec`, then creates the archive. Unzip (or unpack the tarball) and run `strigil` or `strigil-gui` from the `strigil` folder.

## Docker

Light image (CLI only, no GUI):

```bash
docker build -t strigil .
docker run --rm -v "$(pwd)/output:/strigil/output" strigil --url https://example.com --out-dir /strigil/output
```

Override the default URL and options by passing args after the image name.

## CI: build all OS and Docker

On push/PR to `main` or `master`, GitHub Actions:

- Builds PyInstaller bundles on **Ubuntu, macOS, and Windows** and uploads:
  - **strigil-&lt;os&gt;** – the `dist/strigil/` folder
  - **strigil-&lt;os&gt;-install** – install package: `strigil-win.zip`, `strigil-mac.zip`, or `strigil-linux.tar.gz`
- Builds the **Docker** image and runs a quick smoke test.

See [.github/workflows/build.yml](.github/workflows/build.yml).
