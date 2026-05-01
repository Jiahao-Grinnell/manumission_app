from __future__ import annotations

import unittest
from pathlib import Path

from modules.page_classifier.rules import collect_rule_hints, explain_skip_override, override_report_type_from_ocr


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

    def test_i_request_does_not_force_statement(self) -> None:
        text = "I have the honour to say that I request that these slaves deserve liberation."
        self.assertEqual(override_report_type_from_ocr(text, "correspondence"), "correspondence")

    def test_generic_statement_made_by_slave_does_not_force_statement(self) -> None:
        text = "Send papers to Bahrain to check statement made by the slave there."
        self.assertEqual(override_report_type_from_ocr(text, "correspondence"), "correspondence")

    def test_no_person_name_page_is_skip_hint(self) -> None:
        for fixture in ("admin_forwarding_p234.txt", "admin_forwarding_p279.txt"):
            skip = explain_skip_override(_fixture(fixture))
            self.assertTrue(skip["should_skip"], fixture)
            self.assertEqual(skip["skip_reason"], "record_metadata")
            self.assertEqual(skip["applied_by"], "no_person_name_skip_hint")

    def test_any_person_name_prevents_skip_hint(self) -> None:
        text = "Two copies are forwarded to the Political Agent, Kuwait. Sheikh Mubarak is copied for information."
        skip = explain_skip_override(text)
        self.assertFalse(skip["should_skip"])
        self.assertEqual(skip["applied_by"], "person_name_presence_hint")

    def test_index_with_person_name_is_not_skip_hint(self) -> None:
        skip = explain_skip_override(_fixture("index_page.txt"))
        self.assertFalse(skip["should_skip"])


if __name__ == "__main__":
    unittest.main()
