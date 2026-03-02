#!/usr/bin/env bash
# Test Strigil against major digital collections.
# Run from project root: ./scripts/test_collections.sh
# Uses --limit 2, --no-robots (for testing), short delays.
# Some sites block robots.txt; use --no-robots for smoke tests.

set +e  # continue on failure

cd "$(dirname "$0")/.."
OUT="output/test_collections"
mkdir -p "$OUT"

# Common args: limit, no-progress, no-robots (testing), delay
ARGS="--limit 2 --no-progress --delay 0.3 --no-robots"

run() {
  local name="$1"
  local url="$2"
  local extra="${3:-}"
  echo ""
  echo "========== $name =========="
  echo "URL: $url"
  python -m strigil.cli --url "$url" --out-dir "$OUT" $ARGS $extra 2>&1 | tail -30
}

echo "Strigil digital collections smoke test"
echo "Output: $OUT"

# NYPL Digital Collections (IIIF 3, manifest)
run "NYPL" "https://digitalcollections.nypl.org/items/1fbe4680-28ab-013b-27fe-0242ac110002" "--js"

# HathiTrust (imgsrv)
run "HathiTrust" "https://babel.hathitrust.org/cgi/pt?id=hvd.hn3jbn" "--js"

# Library of Congress (link alternate, generic)
run "LOC" "https://www.loc.gov/item/75696521/"

# Internet Archive (IIIF manifest derived) - 404 common for old IDs
run "Internet Archive" "https://archive.org/details/iacl"

# Internet Archive Byrhtferth manuscript (364 images via derived IIIF manifest)
run "Internet Archive Byrhtferth" "https://archive.org/details/TheByrhtferthsManuscriptms17SaintJohnsCollegeOxford" "--limit 5"

# Aalto/Finna (generic HTML - Finnish archives)
run "Aalto Finna" "https://aaltoarkisto.finna.fi/Record/aalto-repository.0db65565-2d79-4c83-8842-ec64d3154b52_b607f2f0-d115-43aa-8ae5-41c6225c496d" "--js"

# Wellcome Collection (Catalogue API -> IIIF manifest)
run "Wellcome" "https://wellcomecollection.org/works/x3knvt2r" "--limit 5"

# Digital Bodleian (IIIF manifest derived from object UUID)
run "Bodleian" "https://digital.bodleian.ox.ac.uk/objects/3458fdcc-ac0b-4b2b-af9c-807f94761e39"

# AALT - Anglo-American Legal Tradition (medieval/early modern legal docs, U Houston)
run "AALT Legal" "http://aalt.law.uh.edu/E1/CP40no1A"

echo ""
echo "========== Done =========="
echo ""
echo "Collections tested: NYPL, HathiTrust, LOC, Internet Archive, Internet Archive Byrhtferth, Aalto Finna, Wellcome (x3knvt2r), Bodleian, AALT (legal)"
echo "Output: $OUT"
