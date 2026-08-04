"""Tests for recovering data from JS-rendered pages without a browser."""
from __future__ import annotations

from strigil.embedded import (
    discover_api_endpoints,
    extract_embedded_json,
    extract_json_ld,
    iter_json_strings,
    looks_js_shelled,
    visible_text_length,
)

NEXT_DATA_PAGE = """
<html><head><title>Item</title></head><body>
<div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"item":{"id":"ms-123","label":"Codex Sangallensis",
"transcription":"Incipit liber de temporum ratione quem Beda composuit."}}},
"page":"/item/[id]"}
</script>
</body></html>
"""

INITIAL_STATE_PAGE = """
<html><body><div id="root"></div>
<script>
window.__INITIAL_STATE__ = {"manuscript":{"shelfmark":"Clm 14456","folios":[{"n":"1r"},{"n":"1v"}]}};
</script>
</body></html>
"""

JSON_LD_PAGE = """
<html><body><h1>A</h1>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Book","name":"De institutione arithmetica"}
</script>
<script type="application/ld+json">
[{"@type":"Person","name":"Boethius"},{"@type":"Person","name":"Nicomachus"}]
</script>
</body></html>
"""

STATIC_PAGE = """
<html><body><article>
<p>Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae, aliam
Aquitani, tertiam qui ipsorum lingua Celtae, nostra Galli appellantur. Hi omnes
lingua institutis legibus inter se differunt. Gallos ab Aquitanis Garumna flumen,
a Belgis Matrona et Sequana dividit. Horum omnium fortissimi sunt Belgae.</p>
</article></body></html>
"""


def test_extract_next_data():
    data = extract_embedded_json(NEXT_DATA_PAGE)
    assert "__NEXT_DATA__" in data.sources
    item = data.sources["__NEXT_DATA__"]["props"]["pageProps"]["item"]
    assert item["id"] == "ms-123"
    assert "Beda" in item["transcription"]


def test_extract_initial_state():
    data = extract_embedded_json(INITIAL_STATE_PAGE)
    assert "__INITIAL_STATE__" in data.sources
    ms = data.sources["__INITIAL_STATE__"]["manuscript"]
    assert ms["shelfmark"] == "Clm 14456"
    # Nested arrays must survive: a non-greedy regex alone would truncate at the
    # first closing brace and lose the folio list entirely.
    assert len(ms["folios"]) == 2


def test_nested_objects_not_truncated():
    """Regression: balanced-delimiter rescan, not the non-greedy regex match."""
    page = (
        '<script>window.__INITIAL_STATE__ = '
        '{"a":{"b":{"c":{"d":"deep"}}},"tail":"kept"};</script>'
    )
    data = extract_embedded_json(page)
    st = data.sources["__INITIAL_STATE__"]
    assert st["a"]["b"]["c"]["d"] == "deep"
    assert st["tail"] == "kept"


def test_extract_json_ld_flattens_lists():
    blocks = extract_json_ld(JSON_LD_PAGE)
    names = {b.get("name") for b in blocks}
    assert names == {"De institutione arithmetica", "Boethius", "Nicomachus"}


def test_json_ld_via_extract_embedded_json():
    data = extract_embedded_json(JSON_LD_PAGE)
    assert len(data.sources["json-ld"]) == 3


def test_looks_js_shelled_true_for_empty_shell():
    assert looks_js_shelled(INITIAL_STATE_PAGE)


def test_looks_js_shelled_false_for_real_content():
    assert not looks_js_shelled(STATIC_PAGE)


def test_looks_js_shelled_false_without_scripts():
    assert not looks_js_shelled("<html><body><p>hi</p></body></html>")


def test_visible_text_length_ignores_scripts():
    n = visible_text_length(NEXT_DATA_PAGE)
    # The Latin transcription lives inside the script tag, so it must not count.
    assert n < 100


def test_discover_api_endpoints_absolutises_and_prioritises_iiif():
    html = """
    <script>
      var cfg = {"manifest":"/iiif/ms-1/manifest.json","search":"/api/v2/search?q="};
      fetch("https://other.example/graphql");
    </script>
    """
    urls = discover_api_endpoints(html, base_url="https://lib.example/item/1")
    assert urls[0] == "https://lib.example/iiif/ms-1/manifest.json"
    assert "https://lib.example/api/v2/search?q=" in urls
    assert "https://other.example/graphql" in urls


def test_discover_api_endpoints_skips_root_relative_without_base():
    urls = discover_api_endpoints('<script>x="/api/v1/items"</script>')
    assert urls == []


def test_discover_api_endpoints_skips_data_uris():
    urls = discover_api_endpoints(
        '<script>x="data:application/json;base64,e30="</script>',
        base_url="https://e.example/",
    )
    assert urls == []


def test_iter_json_strings_finds_long_text():
    obj = {"a": "short", "b": {"c": ["x" * 60, "y"]}, "d": 3}
    found = iter_json_strings(obj, min_length=40)
    assert found == ["x" * 60]


def test_extract_embedded_json_falsy_on_plain_page():
    data = extract_embedded_json(STATIC_PAGE, base_url="https://e.example/")
    assert not data
    assert not data.js_shelled


def test_accepts_bytes():
    data = extract_embedded_json(NEXT_DATA_PAGE.encode("utf-8"))
    assert "__NEXT_DATA__" in data.sources


def test_entity_encoded_payload():
    page = (
        '<script type="application/json">'
        "{&quot;k&quot;:&quot;v&quot;}"
        "</script>"
    )
    data = extract_embedded_json(page)
    assert data.sources["application/json"][0]["k"] == "v"


def test_malformed_json_is_skipped_not_raised():
    page = '<script id="__NEXT_DATA__" type="application/json">{not json,,}</script>'
    data = extract_embedded_json(page)
    assert "__NEXT_DATA__" not in data.sources
