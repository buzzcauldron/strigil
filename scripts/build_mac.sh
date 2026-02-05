#!/usr/bin/env bash
# Build macOS install package. Run from project root:
#   ./scripts/build_mac.sh
# Output: dist/basic-scraper-mac.zip (and dist/basic-scraper/)

set -e
cd "$(dirname "$0")/.."
echo "Building basic-scraper for macOS..."
pip install -e ".[bundle]" -q
pyinstaller basic-scraper.spec
DIST=dist/basic-scraper
OUT=dist/basic-scraper-mac.zip
rm -f "$OUT"
(cd dist && zip -r basic-scraper-mac.zip basic-scraper)
echo "Done: $OUT"
