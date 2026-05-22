"""Tests for EEBO and ECCO archive adapters."""

from __future__ import annotations

import unittest

from strigil.adapters.eebo import EeboAdapter, _identifiers_from, _ia_search_queries
from strigil.adapters.ecco import EccoAdapter, _identifiers_from as ecco_idents
from strigil.adapters.ecco import _ia_search_queries as ecco_queries
from strigil.adapters.ia_lookup import clean_record_title, probe_internet_archive
from strigil.extractors import _ECCO_DOMAIN_RE, _EEBO_DOMAIN_RE


class TestDomainMatch(unittest.TestCase):
    def test_eebo_matches_proquest_path(self) -> None:
        self.assertTrue(
            EeboAdapter().matches("https://www.proquest.com/eebo/docview/12345"),
        )

    def test_ecco_matches_link_gale(self) -> None:
        self.assertTrue(
            EccoAdapter().matches(
                "https://link.gale.com/apps/ECCO?u=uni&id=ABC123",
            ),
        )

    def test_ecco_regex_go_gale(self) -> None:
        self.assertTrue(
            _ECCO_DOMAIN_RE.search("https://go.gale.com/ps/i.do?p=ECCO&u=lib"),
        )


class TestIdentifiers(unittest.TestCase):
    def test_eebo_stc_and_wing(self) -> None:
        html = "<p>STC (II) A1234 and STC 9876</p>"
        idents = _identifiers_from("", html)
        self.assertIn("wing", idents)
        self.assertIn("stc", idents)

    def test_ecco_estc_and_doc_id(self) -> None:
        url = "https://link.gale.com/apps/ECCO?docId=CW123456"
        idents = ecco_idents(url, "")
        self.assertEqual(idents.get("doc_id"), "CW123456")
        self.assertEqual(idents.get("estc"), None)

    def test_ecco_estc_in_html(self) -> None:
        idents = ecco_idents("", "<span>ESTC S123456</span>")
        self.assertEqual(idents["estc"], "S123456")


class TestIaQueries(unittest.TestCase):
    def test_eebo_title_query(self) -> None:
        qs = _ia_search_queries({"stc": "1234"}, title="Some Book")
        self.assertTrue(any("Some Book" in q for q in qs))

    def test_ecco_estc_query(self) -> None:
        qs = ecco_queries({"estc": "T999"}, title=None)
        self.assertTrue(any("T999" in q for q in qs))


class TestCleanTitle(unittest.TestCase):
    def test_strips_gale_suffix(self) -> None:
        self.assertEqual(
            clean_record_title("Pamphlet on trade - Gale"),
            "Pamphlet on trade",
        )


class TestProbeIa(unittest.TestCase):
    def test_no_fetch_returns_empty(self) -> None:
        self.assertEqual(probe_internet_archive(None, ['title:"foo"']), [])


class TestProquestPdfExtraction(unittest.TestCase):
    def test_finds_pdf_href(self) -> None:
        from strigil.adapters.eebo import _try_proquest_pdf

        html = '<a href="/eebo/fulltextPDF/123">PDF</a>'
        urls = _try_proquest_pdf("https://www.proquest.com/eebo/", html)
        self.assertTrue(urls)
        self.assertIn("proquest.com", urls[0])


class TestGalePdfExtraction(unittest.TestCase):
    def test_finds_gale_pdf_href(self) -> None:
        from strigil.adapters.ecco import _try_gale_pdf

        html = '<a href="/apps/downloadPDF?docId=x">PDF</a>'
        urls = _try_gale_pdf("https://link.gale.com/apps/ECCO", html)
        self.assertTrue(urls)
        self.assertIn("gale.com", urls[0])


if __name__ == "__main__":
    unittest.main()
