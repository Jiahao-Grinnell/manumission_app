from __future__ import annotations

import unittest
from pathlib import Path

from modules.name_extractor.merging import canonicalize_candidate_name, looks_like_subject_label
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

    def test_hallucinated_evidence_cannot_keep_absent_name(self) -> None:
        text = _fixture("admin_forwarding_p279.txt")
        decision = explain_candidate_decision("Mubarak", "the following refugee slaves: Mubarak", text)
        self.assertFalse(decision["keep"])
        self.assertEqual(decision["reason_type"], "name_absent_from_ocr")

    def test_following_slave_list_still_keeps_ocr_names(self) -> None:
        text = _fixture("grouped_list.txt")
        final_people, removed, _ = apply_rule_filter(
            [
                {"name": "Ahmad bin Said", "evidence": "The following refugee slaves request repatriation"},
                {"name": "Fatima bint Ali", "evidence": "The following refugee slaves request repatriation"},
            ],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Ahmad bin Said", "Fatima bint Ali"])
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

    def test_canonicalizes_role_and_dangling_kinship_fragments(self) -> None:
        self.assertEqual(canonicalize_candidate_name("Salem slave of Mohamed bin Abdulla el Hejazi"), "Salem")
        self.assertEqual(canonicalize_candidate_name("Abdulla, son of"), "Abdulla")
        self.assertEqual(canonicalize_candidate_name("Abdulla's son"), "Abdulla's son")
        self.assertEqual(canonicalize_candidate_name("Nuru, original name Gangaram son of Hanuman"), "Nuru")
        self.assertTrue(looks_like_subject_label("Salem's sister"))
        self.assertTrue(looks_like_subject_label("son of Salem"))
        self.assertFalse(looks_like_subject_label("his sister"))

    def test_relation_labeled_subjects_with_named_anchor_survive(self) -> None:
        text = _fixture("relation_subject_labels.txt")
        final_people, removed, _ = apply_rule_filter(
            [
                {"name": "Salem's sister", "evidence": "Salem's sister was kidnapped"},
                {"name": "son of Salem", "evidence": "son of Salem was sold"},
                {"name": "Ahmed's sister", "evidence": "people who know Ahmed's sister"},
                {"name": "his sister", "evidence": "His sister was kept as a slave"},
                {"name": "Three Slaves", "evidence": "Three slaves took shelter"},
                {"name": "A Number of Slaves", "evidence": "a number of slaves took shelter"},
            ],
            text,
        )
        self.assertEqual({item["name"] for item in final_people}, {"Salem's sister", "son of Salem"})
        self.assertTrue({"Ahmed's sister", "His Sister"} <= {item["name"] for item in removed})
        self.assertNotIn("Three Slaves", {item["name"] for item in final_people})
        self.assertNotIn("A Number of Slaves", {item["name"] for item in final_people})

    def test_rule_seeds_relation_labels_and_named_subject_forms(self) -> None:
        text = _fixture("relation_subject_labels.txt") + "\n" + _fixture("full_p012_abdulla_statement.txt")
        self.assertTrue({"Salem's sister", "son of Salem", "Abdulla"} <= {item["name"] for item in rule_seed_candidates(text)})

    def test_full_p013_keeps_abdulla_without_dangling_son_of(self) -> None:
        text = _fixture("full_p013_abdulla_son_of.txt")
        final_people, removed, _ = apply_rule_filter(
            [{"name": "Abdulla, son of", "evidence": "Description of slave Abdulla for entry"}],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Abdulla"])
        self.assertEqual(removed, [])

    def test_full_p042_and_p046_keep_salem_only(self) -> None:
        for fixture, owner_name in (
            ("full_p042_salem_slave_of.txt", "Mohamed bin Abdulla el Hejazi"),
            ("full_p046_salem_slave_of.txt", "Muhammad bin Abdullah-el-Hejazi"),
        ):
            with self.subTest(fixture=fixture):
                text = _fixture(fixture)
                final_people, _, _ = apply_rule_filter(
                    [
                        {"name": "Salem Slave of " + owner_name, "evidence": "Salem slave of " + owner_name},
                        {"name": "Salem", "evidence": "Salem slave of " + owner_name},
                        {"name": "Mohamed el Howeik", "evidence": "imported slaves for sale"},
                        {"name": "Ahmed Mohamed Bozeineh", "evidence": "imported slaves for sale"},
                    ],
                    text,
                )
                self.assertEqual([item["name"] for item in final_people], ["Salem"])

    def test_full_p116_papers_from_names_are_not_subjects(self) -> None:
        text = _fixture("full_p116_papers_from.txt")
        final_people, removed, _ = apply_rule_filter(
            [
                {"name": "Laypied Leeds", "evidence": "take the Shares' manumission papers from Laypied Leeds"},
                {"name": "Sayyid Leid", "evidence": "take the Shia's manumission papers from Sayyid Leid"},
            ],
            text,
        )
        self.assertEqual(final_people, [])
        self.assertEqual({item["name"] for item in removed}, {"Laypied Leeds", "Sayyid Leid"})

    def test_full_p166_witness_persons_stay_empty(self) -> None:
        text = _fixture("full_p166_witness_persons.txt")
        final_people, _, _ = apply_rule_filter(
            [
                {"name": "Abdullah Sudani", "evidence": "three persons named Abdullah Sudani"},
                {"name": "Dawail Somali", "evidence": "three persons named Dawail Somali"},
                {"name": "Faraj Krus Kah", "evidence": "three persons named Faraj Krus Kah"},
            ],
            text,
        )
        self.assertEqual(final_people, [])

    def test_full_p180_keeps_named_sister_subject_only(self) -> None:
        text = _fixture("full_p180_saidah_sale.txt")
        final_people, _, _ = apply_rule_filter(
            [
                {"name": "Rashid bin Muhammad", "evidence": "Rashid bin Muhammad states"},
                {"name": "Khaimullah", "evidence": "Khaimullah and his sister Sa'idah"},
                {"name": "Sa'idah", "evidence": "his sister Sa'idah"},
                {"name": "Saleh", "evidence": "recognised Saleh at Tamburah"},
            ],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Sa'idah"])

    def test_full_sarur_brishari_said_bilal_bashir_subject_contexts(self) -> None:
        cases = [
            (
                "full_p244_sarur.txt",
                [
                    {"name": "Sarur", "evidence": "the man Sarur is not his slave"},
                    {"name": "Sarur's wife", "evidence": "former husband of Sarur's wife"},
                    {"name": "Sa'ud al-'Asfur", "evidence": "Nakhuda named Sa'ud al-'Asfur"},
                ],
                {"Sarur"},
            ),
            (
                "full_p258_brishari.txt",
                [
                    {"name": "Brishari bin Brubarak", "evidence": "A negro man, Brishari bin Brubarak, by name"},
                    {"name": "Brubarak bin Abdul Aziz bin Nasir", "evidence": "master Brubarak bin Abdul Aziz"},
                    {"name": "Ali bin Rashid", "evidence": "servant (not a slave) named Ali bin Rashid"},
                ],
                {"Brishari bin Brubarak"},
            ),
            ("full_p260_said.txt", [{"name": "Sa'id", "evidence": "Name. Sa'id"}, {"name": "Abdullah bin Gai", "evidence": "sold me to Abdullah bin Gai"}], {"Sa'id"}),
            ("full_p284_bilal.txt", [{"name": "Bilal", "evidence": "negro man by the name of Bilal"}], {"Bilal"}),
            ("full_p288_bashir.txt", [{"name": "Bashir", "evidence": "He is named Bashir"}], {"Bashir"}),
        ]
        for fixture, candidates, expected in cases:
            with self.subTest(fixture=fixture):
                final_people, _, _ = apply_rule_filter(candidates, _fixture(fixture))
                self.assertEqual({item["name"] for item in final_people}, expected)

    def test_full_p242_keeps_statement_subject_not_family_only_son(self) -> None:
        text = _fixture("full_p242_sarpor_matook.txt")
        final_people, _, _ = apply_rule_filter(
            [
                {"name": "Sarpor bin Salim", "evidence": "Statement of Sarpor bin Salim"},
                {"name": "Matook", "evidence": "I have a son aged 22 years named Matook"},
            ],
            text,
        )
        self.assertEqual([item["name"] for item in final_people], ["Sarpor bin Salim"])

    def test_full_memorandum_pages_keep_victims_and_exclude_traffickers(self) -> None:
        cases = [
            (
                "full_p273_memorandum.txt",
                [
                    "Moti",
                    "Shankar",
                    "Gangaram son of Hanuman Bania of Tatta",
                    "Nuru, original name Gangaram son of Hanuman",
                    "Musa bin Haider",
                    "Muhammed bin Davud",
                    "Reza bin Rahmat",
                    "Muhammed Mekrani",
                    "Abdullah Muhammed Dawwar",
                ],
                {"Moti", "Shankar", "Nuru", "Musa bin Haider", "Muhammed bin Davud", "Reza bin Rahmat"},
            ),
            (
                "full_p274_memorandum.txt",
                [
                    "Mirza son of Ghulam Hussein",
                    "Shambeh bin Shahim",
                    "Khamis bin Ibrahim",
                    "Zahra daughter of Ahmed",
                    "Bibak daughter of Muhammed Rudbari",
                    "Amnah daughter of Muhammed",
                    "Ghulam son of Qasim Ali Hijlasaz",
                    "Ali bin Saif",
                    "Muhin bin Mubarak Baluchi",
                ],
                {
                    "Mirza son of Ghulam Hussein",
                    "Shambeh bin Shahim",
                    "Khamis bin Ibrahim",
                    "Zahra daughter of Ahmed",
                    "Bibak daughter of Muhammed Rudbari",
                    "Amnah daughter of Muhammed",
                    "Ghulam son of Qasim Ali Hijlasaz",
                },
            ),
            (
                "full_p275_relations.txt",
                ["Ashraf", "Hamal", "Waluk", "Mohamed Khan", "Muhammed Dawwar", "Ismail", "Karimdad"],
                {"Ashraf", "Hamal", "Waluk"},
            ),
        ]
        for fixture, names, expected in cases:
            with self.subTest(fixture=fixture):
                candidates = [{"name": name, "evidence": name} for name in names]
                final_people, _, _ = apply_rule_filter(candidates, _fixture(fixture))
                self.assertEqual({item["name"] for item in final_people}, expected)

    def test_full_p268_unnamed_group_stays_empty(self) -> None:
        text = _fixture("full_p268_unnamed.txt")
        final_people, _, _ = apply_rule_filter(
            [
                {"name": "The Three Females", "evidence": "three females with two children"},
                {"name": "Three Females With Two Children", "evidence": "three females with two children"},
                {"name": "A Number of Slaves", "evidence": "a number of slaves"},
            ],
            text,
        )
        self.assertEqual(final_people, [])


if __name__ == "__main__":
    unittest.main()
