# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (Reserved for future changes.)

## [0.5.0] - 2025-03-02

### Added
- **Internet Archive fix**: archive.org/details URLs now yield all images (e.g. 364 for Byrhtferth manuscript) via derived IIIF manifest. Previously only 1 thumbnail was found because manifest logic was never invoked.
- **archive.org metadata API fallback** (`strigil/archive_org.py`): When IIIF manifest fails, fetches image list from `archive.org/metadata/{id}` and constructs download URLs.
- **Wellcome Collection adapter** (`strigil/adapters/wellcome.py`): Uses Catalogue API to fetch IIIF manifest URL, then parses manifest for all canvases (e.g. 58 images for Computistical miscellany).
- **Extensible archive adapter architecture** (`strigil/adapters/`): Protocol-based adapters for archive-specific extraction; register new adapters without modifying core schema.
- **Discovery context and user hints**: `--expected-images N` hints that the page should have ~N images (triggers fallbacks if fewer found); `--source ADAPTER` forces a specific adapter (e.g. `wellcome`, `archive_org`).
- **Fallback strategy chain**: When results fall short of expectations (user or inferred), tries adapter-based extraction.
- **HTML heuristics** (`infer_expected_images`): Parses "Contains: N images", "X/Y" pagination, "X of Y" to infer expected count and trigger fallbacks automatically.

### Changed
- Schema detection now merges derived IIIF manifest URLs (archive.org, Bodleian, Stanford PURL) with HTML-found manifests.
- Added ARCHIVE_ORG and WELLCOME to ImageSchema enum.

## [0.4.0] - 2025-02-06

### Added
- **Schema detection pipeline** (`strigil/schema.py`): Detects image storage schema (CONTENTdm, NYPL, IIIF manifest, generic HTML) and runs the appropriate extractor for full-resolution images.
- **NYPL Digital Collections**: Full support for 112+ canvases per item via `api-collections.nypl.org` manifest; IIIF 3 parsing with `rendering` and top-level Canvas items.
- **FlareSolverr integration** (`strigil/flaresolverr.py`): Optional `--flaresolverr` / `FLARESOLVERR_URL` to fetch HTML via [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) and bypass Cloudflare; GUI checkbox and URL field.
- **Rate-limit handling**: Automatic throttling on HTTP 429 or rate-limit text in response body; honors `Retry-After` (seconds or HTTP-date); per-Fetcher backoff with decay after success.
- Playwright and Chromium as default dependencies; Chromium installs automatically on first `--js` use.

### Changed
- Renamed package from basic-scraper to strigil. Install with `pip install strigil`.
- Config dir: `~/.basic-scraper/` → `~/.strigil/`. Env var: `BASIC_SCRAPER_AUTO_INSTALL_DEPS` → `STRIGIL_AUTO_INSTALL_DEPS`.
- Auto-install dependencies on first run by default; optional deps (playwright, tqdm, readability-lxml) install and app continues. Set `STRIGIL_AUTO_INSTALL_DEPS=0` to disable.
- Discovery delegates to schema pipeline; CONTENTdm, NYPL, IIIF manifest, and generic HTML run in priority order.
- JS support: no longer requires extra install or manual `playwright install`.
- Crawl with `--js` or `--flaresolverr` uses one worker for reliable rendering.
- GUI: "Use JavaScript" checkbox (default on); "FlareSolverr (Cloudflare bypass)" option.
- README: Cloudflare/FlareSolverr section, rate limits and throttling, install notes; repository branding to strigil.

## [0.3.0] - 2025-02-04

### Added
- Clear progress output: "Found: X PDFs, Y images", "→ Downloading N assets", "[i/N]" per item.
- Image extraction: `link[rel=preload][as=image]`, CSS `background-image: url()`, extension-less paths (e.g. `/image/`, `/thumb/`), more lazy-load attrs (`data-zoom-src`, `data-hires`, etc.).
- GUI: Stop button, status parsing for mapping/download progress, `[i/N]` display.
- Skip HEAD when no `--min-image-size` / `--max-image-size` (faster image downloads).
- Auto retry crawl with cross-domain if same-domain returns no results.
- File skip: check canonical paths and skip already-scraped images/PDFs/text.
- Last URL persisted on GUI relaunch.
- Multiprocessing semaphore warning suppression; tqdm unit spacing fix.

### Changed
- Default delay: 1.0s → 0.5s.
- Workers: `SAFE_ASSET_WORKERS` 3→5, `SAFE_HEAD_WORKERS` 2→4; parallel HEAD threshold 8→4.
- Crawl follows links by default; "Follow links" checkbox clarified.
- README: min-image-size tip; `output_*/` in .gitignore.

### Removed
- GUI progress bar and spinner (replaced by clearer status text).

## [0.2.0] - 2025-02-04

- GUI (tkinter) with file-type selector, image size filter, and Open folder button.
- CLI: `--types` (pdf/text/images), `--min-image-size` / `--max-image-size`, `--workers`, `--no-progress`.
- Auto-install deps via `STRIGIL_AUTO_INSTALL_DEPS=1`.
- Hardware-autodetected parallel crawl; progress bar (optional tqdm).
- Docker image (slim) and GitHub Actions (PyInstaller + Docker).
- License: MIT, Copyright (c) 2025 Seth Strickland.

## [0.1.0] - 2025-02-04

- Initial package layout, CLI stub, and semver setup.
