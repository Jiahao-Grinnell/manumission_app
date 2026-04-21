from __future__ import annotations

import unittest
from pathlib import Path

from modules.name_extractor.rules import apply_rule_filter, explain_candidate_decision, is_freeborn_not_slave_name


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RulesTests(unittest.TestCase):
    def test_positive_subject_signal_keeps_statement_subject(self) -> None:
        text = _fixture("single_subject.txt")
        decision = explain_candidate_decision("Mariam bint Yusuf", "Statement of slave Mariam bint Yusuf", text)
        self.assertTrue(decision["keep"])
        self.assertEqual(decision["reason_type"], "positive_rule")

    def test_negative_role_signal_drops_buyer(self) -> None:
        text = _fixture("owner_vs_slave.txt")
        decision = explain_candidate_decision("Sheikh Rashid", "sold to one Sheikh Rashid", text)
        self.assertFalse(decision["keep"])
        self.assertEqual(decision["reason_type"], "negative_rule")

    def test_freeborn_context_is_removed(self) -> None:
        text = _fixture("freeborn_page.txt")
        self.assertTrue(is_freeborn_not_slave_name("Salim bin Hamad", text))

    def test_apply_rule_filter_returns_removed_reason_rows(self) -> None:
        text = _fixture("owner_vs_slave.txt")
        final_people, removed, kept_reasons = apply_rule_filter(
            [
                {"name": "Mariam bint Yusuf", "evidence": "statement of Mariam bint Yusuf"},
                {"name": "Sheikh Rashid", "evidence": "sold to one Sheikh Rashid"},
            ],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Mariam bint Yusuf"])
        self.assertEqual(removed[0]["stage"], "rule_filter")
        self.assertEqual(kept_reasons[0]["stage"], "rule_filter")


if __name__ == "__main__":
    unittest.main()
