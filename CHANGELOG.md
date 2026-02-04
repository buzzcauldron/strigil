# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2025-02-04

- GUI (tkinter) with file-type selector, image size filter, and Open folder button.
- CLI: `--types` (pdf/text/images), `--min-image-size` / `--max-image-size`, `--workers`, `--no-progress`.
- Auto-install deps via `BASIC_SCRAPER_AUTO_INSTALL_DEPS=1`.
- Hardware-autodetected parallel crawl; progress bar (optional tqdm).
- Docker image (slim) and GitHub Actions (PyInstaller + Docker).
- License: MIT, Copyright (c) 2025 Seth Strickland.

## [0.1.0]

- Initial package layout, CLI stub, and semver setup.
