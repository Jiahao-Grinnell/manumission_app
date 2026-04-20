from __future__ import annotations

import unittest

from modules.normalizer.dates import extract_doc_year, parse_first_date_in_text, to_iso_date


class DateTests(unittest.TestCase):
    def test_iso_date(self) -> None:
        self.assertEqual(to_iso_date("1931-05-17"), ("1931-05-17", "explicit"))

    def test_dash_date(self) -> None:
        self.assertEqual(to_iso_date("17-5-1931"), ("1931-05-17", "explicit"))

    def test_written_date(self) -> None:
        self.assertEqual(to_iso_date("17th May 1931"), ("1931-05-17", "explicit"))
        self.assertEqual(to_iso_date("May 17, 1931"), ("1931-05-17", "explicit"))

    def test_doc_year_fallback(self) -> None:
        self.assertEqual(to_iso_date("17th May", 1931), ("1931-05-17", "derived_from_doc"))

    def test_parse_first_date_and_extract_year(self) -> None:
        self.assertEqual(parse_first_date_in_text("arrived about the 17th May", 1931), ("1931-05-17", "derived_from_doc", "17th May"))
        self.assertEqual(extract_doc_year("Political Agency, 1931"), 1931)


if __name__ == "__main__":
    unittest.main()
