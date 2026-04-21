from __future__ import annotations

import unittest

from modules.page_classifier.parsing import choose_report_type, parse_page_decision


class ParsingTests(unittest.TestCase):
    def test_choose_report_type_maps_legacy_value(self) -> None:
        self.assertEqual(choose_report_type("official correspondence"), "correspondence")
        self.assertEqual(choose_report_type("transport/admin"), "correspondence")

    def test_parse_page_decision_defaults_invalid_shape(self) -> None:
        decision = parse_page_decision(["not", "a", "dict"])
        self.assertTrue(decision.should_extract)
        self.assertIsNone(decision.skip_reason)
        self.assertEqual(decision.report_type, "correspondence")

    def test_parse_page_decision_truncates_evidence_and_normalizes_skip(self) -> None:
        decision = parse_page_decision(
            {
                "should_extract": True,
                "skip_reason": " INDEX ",
                "report_type": "statement",
                "evidence": " ".join(["word"] * 40),
            }
        )
        self.assertFalse(decision.should_extract)
        self.assertEqual(decision.skip_reason, "index")
        self.assertEqual(len(decision.evidence.split()), 25)

    def test_parse_page_decision_falls_back_for_unknown_report_type(self) -> None:
        decision = parse_page_decision(
            {
                "should_extract": False,
                "skip_reason": None,
                "report_type": "memo",
                "evidence": "memo page",
            }
        )
        self.assertFalse(decision.should_extract)
        self.assertEqual(decision.report_type, "correspondence")


if __name__ == "__main__":
    unittest.main()
