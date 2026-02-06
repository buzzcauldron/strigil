#!/usr/bin/env bash
# Build Linux install package. Run from project root:
#   ./scripts/build_linux.sh
# Output: dist/strigil-linux.tar.gz (and dist/strigil/)

set -e
cd "$(dirname "$0")/.."
echo "Building strigil for Linux..."
pip install -e ".[bundle]" -q
pyinstaller strigil.spec
DIST=dist/strigil
OUT=dist/strigil-linux.tar.gz
rm -f "$OUT"
tar -czf "$OUT" -C dist strigil
echo "Done: $OUT"
