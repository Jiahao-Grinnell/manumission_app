from __future__ import annotations

import unittest
from pathlib import Path

from modules.page_classifier.rules import collect_rule_hints, override_report_type_from_ocr


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RulesTests(unittest.TestCase):
    def test_statement_pattern_overrides_report_type(self) -> None:
        text = _fixture("statement_page.txt")
        self.assertEqual(override_report_type_from_ocr(text, "correspondence"), "statement")

    def test_correspondence_pattern_overrides_report_type(self) -> None:
        text = _fixture("transport_page.txt")
        self.assertEqual(override_report_type_from_ocr(text, "statement"), "correspondence")

    def test_collect_rule_hints_marks_index_and_bad_ocr(self) -> None:
        index_hints = collect_rule_hints(_fixture("index_page.txt"))
        bad_hints = collect_rule_hints(_fixture("bad_ocr_page.txt"))
        self.assertTrue(index_hints["index_skip_hint"]["matched"])
        self.assertTrue(bad_hints["bad_ocr_skip_hint"]["matched"])

    def test_correspondence_fixture_has_no_override(self) -> None:
        text = _fixture("correspondence_page.txt")
        self.assertEqual(override_report_type_from_ocr(text, "correspondence"), "correspondence")


if __name__ == "__main__":
    unittest.main()
