#!/usr/bin/env bash
# Build macOS install package. Run from project root:
#   ./scripts/build_mac.sh
# Output: dist/strigil-mac.zip (and dist/strigil/)

set -e
cd "$(dirname "$0")/.."
echo "Building strigil for macOS..."
pip install -e ".[bundle]" -q
pyinstaller strigil.spec
DIST=dist/strigil
OUT=dist/strigil-mac.zip
rm -f "$OUT"
(cd dist && zip -r strigil-mac.zip strigil)
echo "Done: $OUT"
