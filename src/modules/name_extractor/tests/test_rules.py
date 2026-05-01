from __future__ import annotations

import unittest
from pathlib import Path

from modules.name_extractor.rules import apply_rule_filter, explain_candidate_decision, is_freeborn_not_slave_name, rule_seed_candidates


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
        decision = explain_candidate_decision("Rashid bin Hamad", "sold to one Rashid bin Hamad", text)
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
                {"name": "Rashid bin Hamad", "evidence": "sold to one Rashid bin Hamad"},
            ],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Mariam bint Yusuf"])
        self.assertEqual(removed[0]["stage"], "rule_filter")
        self.assertEqual(kept_reasons[0]["stage"], "rule_filter")

    def test_plural_slave_name_list_survives_rule_filter(self) -> None:
        text = _fixture("sample2_p010_subject_list.txt")
        candidates = [
            {"name": "Aman bin Faragh", "evidence": "three other slaves whose names are: 1. Aman bin Faragh."},
            {"name": "Zuwaid bin Mabrook", "evidence": "three other slaves whose names are: 2. Zuwaid bin Mabrook."},
            {"name": "Daaji bin Khamis", "evidence": "three other slaves whose names are: 3. Daaji bin Khamis."},
        ]
        final_people, removed, _ = apply_rule_filter(candidates, text)
        self.assertEqual({item["name"] for item in final_people}, {"Aman bin Faragh", "Zuwaid bin Mabrook", "Daaji bin Khamis"})
        self.assertEqual(removed, [])

    def test_rule_seed_candidates_recovers_certain_negro_and_subject_list(self) -> None:
        text = _fixture("sample2_p010_subject_list.txt")
        self.assertEqual(
            {item["name"] for item in rule_seed_candidates(text)},
            {"Surur", "Aman bin Faragh", "Zuwaid bin Mabrook", "Daaji bin Khamis"},
        )

    def test_generic_the_slave_is_rejected(self) -> None:
        text = _fixture("sample2_p014_generic_slave.txt")
        decision = explain_candidate_decision(
            "The Slave",
            "Send pps to Bahrain to check statement made by the slave there.",
            text,
        )
        self.assertFalse(decision["keep"])
        self.assertEqual(decision["reason_type"], "generic_subject_phrase")

    def test_case_of_the_negro_is_not_dropped_as_official_title(self) -> None:
        text = _fixture("sample1_p011_abdulla.txt")
        final_people, removed, _ = apply_rule_filter(
            [{"name": "Abdulla", "evidence": "negro Abdulla no longer desires a manumission certificate"}],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Abdulla"])
        self.assertEqual(removed, [])

    def test_master_and_servant_continuations_stay_excluded(self) -> None:
        text = _fixture("sample1_non_subject_continuations.txt")
        final_people, removed, _ = apply_rule_filter(
            [
                {"name": "Abdullah Al-ahmad", "evidence": "master named Abdullah Al-ahmad"},
                {"name": "Saad Albu Shaitan", "evidence": "servant Saad Albu Shaitan"},
            ],
            text,
        )
        self.assertEqual(final_people, [])
        self.assertEqual({item["name"] for item in removed}, {"Abdullah Al-ahmad", "Saad Albu Shaitan"})


if __name__ == "__main__":
    unittest.main()
