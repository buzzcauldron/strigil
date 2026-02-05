#!/usr/bin/env bash
# Build Linux install package. Run from project root:
#   ./scripts/build_linux.sh
# Output: dist/basic-scraper-linux.tar.gz (and dist/basic-scraper/)

set -e
cd "$(dirname "$0")/.."
echo "Building basic-scraper for Linux..."
pip install -e ".[bundle]" -q
pyinstaller basic-scraper.spec
DIST=dist/basic-scraper
OUT=dist/basic-scraper-linux.tar.gz
rm -f "$OUT"
tar -czf "$OUT" -C dist basic-scraper
echo "Done: $OUT"
