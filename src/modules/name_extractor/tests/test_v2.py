from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from modules.name_extractor.core import extract_names


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)

    def generate_json(self, prompt: str, schema_hint: str, stats, *, num_predict: int | None = None):  # noqa: ANN001
        stats.model_calls += 1
        if not self.responses:
            return {"labels": []}
        return self.responses.pop(0)


class V2PipelineTests(unittest.TestCase):
    def test_subject_list_governing_context_keeps_all_names_without_local_role_label(self) -> None:
        result = extract_names(_fixture("sample2_p010_subject_list.txt"), report_type="correspondence", client=FakeClient([{"names": []}]))

        self.assertEqual(
            {item["name"] for item in result.named_people},
            {"Surur", "Aman bin Faragh", "Zuwaid bin Mabrook", "Daaji bin Khamis"},
        )
        self.assertIn("context_bundle", result.passes)
        self.assertTrue(any(reason["name"] == "Aman bin Faragh" for reason in result.final_reasons))

    def test_relation_label_can_use_expanded_same_page_context(self) -> None:
        text = (
            "Salem gave information to the Agent.\n"
            "This record concerns Salem's sister.\n"
            "She was kidnapped from the coast and sold as a slave before she came here.\n"
        )
        client = FakeClient(
            [
                {"names": [{"name": "Salem's sister", "span_quote": "record concerns Salem's sister"}]},
                {
                    "labels": [
                        {
                            "candidate_id": "cand_001",
                            "role": "relation_subject",
                            "confidence": "high",
                            "evidence_quote": "She was kidnapped from the coast and sold as a slave",
                        }
                    ]
                },
            ]
        )

        result = extract_names(text, report_type="statement", client=client)

        self.assertEqual([item["name"] for item in result.named_people], ["Salem's sister"])
        self.assertTrue(any("relation_subject" in str(signal) for reason in result.final_reasons for signal in reason["signals"]))

    def test_invalid_role_evidence_does_not_keep_ambiguous_llm_candidate(self) -> None:
        text = "Letter from Rashid bin Hamad about office arrangements."
        client = FakeClient(
            [
                {"names": [{"name": "Rashid bin Hamad", "span_quote": "Letter from Rashid bin Hamad"}]},
                {
                    "labels": [
                        {
                            "candidate_id": "cand_001",
                            "role": "enslaved_subject",
                            "confidence": "high",
                            "evidence_quote": "Rashid was kidnapped and sold",
                        }
                    ]
                },
            ]
        )

        result = extract_names(text, report_type="correspondence", client=client)

        self.assertEqual(result.named_people, [])
        self.assertTrue(any(item["reason_type"] in {"low_subject_score", "ambiguous_role", "hard_negative_role"} for item in result.removed_candidates))


if __name__ == "__main__":
    unittest.main()
