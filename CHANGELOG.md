# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Auto-install deps via `BASIC_SCRAPER_AUTO_INSTALL_DEPS=1`.
- Hardware-autodetected parallel crawl; progress bar (optional tqdm).
- Docker image (slim) and GitHub Actions (PyInstaller + Docker).
- License: MIT, Copyright (c) 2025 Seth Strickland.

## [0.1.0]

- Initial package layout, CLI stub, and semver setup.
