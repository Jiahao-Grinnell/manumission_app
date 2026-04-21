from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from modules.metadata_extractor.core import run_folder, run_page_file


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.waited = False

    def generate_json(self, prompt: str, schema_hint: str, stats, *, num_predict: int | None = None):  # noqa: ANN001
        stats.model_calls += 1
        if not self.responses:
            raise AssertionError("No fake response configured")
        return self.responses.pop(0)

    def wait_ready(self, timeout_s: int = 240) -> None:
        self.waited = True


class CoreTests(unittest.TestCase):
    def test_run_page_file_extracts_all_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p001.txt"
            classify = root / "p001.classify.json"
            names = root / "p001.names.json"
            target = root / "p001.meta.json"
            ocr.write_text(_fixture("kidnapping_abuse.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}), encoding="utf-8")
            names.write_text(
                json.dumps({"page": 1, "named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Fatima bint Ali", "evidence": "Fatima bint Ali"}]}),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    {"report_type": "statement", "crime_type": "kidnapping", "whether_abuse": "yes", "conflict_type": None, "trial": "manumission requested", "amount_paid": None, "evidence": {"crime_type": "kidnapped from Zanzibar", "whether_abuse": "beaten severely by her owner", "trial": "requests freedom"}},
                    {"report_type": "statement", "crime_type": None, "whether_abuse": "", "conflict_type": None, "trial": None, "amount_paid": None, "evidence": {}},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client)
            self.assertEqual(len(result.rows), 2)
            self.assertEqual(result.rows[0]["Name"], "Mariam bint Yusuf")
            self.assertEqual(result.rows[0]["Crime Type"], "kidnapping")
            self.assertTrue(target.exists())

    def test_run_page_file_upserts_single_person_without_dropping_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p001.txt"
            classify = root / "p001.classify.json"
            names = root / "p001.names.json"
            target = root / "p001.meta.json"
            ocr.write_text(_fixture("certificate_grant.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "correspondence", "evidence": "Memorandum regarding Fatima bint Ali"}), encoding="utf-8")
            names.write_text(
                json.dumps({"page": 1, "named_people": [{"name": "Fatima bint Ali", "evidence": "Fatima bint Ali"}, {"name": "Ahmad bin Said", "evidence": "Ahmad bin Said"}]}),
                encoding="utf-8",
            )
            target.write_text(
                json.dumps(
                    {
                        "page": 1,
                        "report_type": "correspondence",
                        "classify": {"report_type": "correspondence"},
                        "names": ["Fatima bint Ali", "Ahmad bin Said"],
                        "people": [
                            {
                                "name": "Fatima bint Ali",
                                "row": {"Name": "Fatima bint Ali", "Page": 1, "Report Type": "correspondence", "Crime Type": "", "Whether abuse": "", "Conflict Type": "", "Trial": "", "Amount paid": "", "_evidence": {}},
                                "validation": {},
                                "raw_values": {},
                                "rendered_prompt": "old",
                                "response_json": {},
                                "model_calls": 1,
                                "repair_calls": 0,
                                "elapsed_seconds": 1.0,
                            }
                        ],
                        "rows": [{"Name": "Fatima bint Ali", "Page": 1, "Report Type": "correspondence", "Crime Type": "", "Whether abuse": "", "Conflict Type": "", "Trial": "", "Amount paid": ""}],
                        "model_calls": 1,
                        "repair_calls": 0,
                        "elapsed_seconds": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    {"report_type": "correspondence", "crime_type": None, "whether_abuse": "no", "conflict_type": None, "trial": "freedom/manumission outcome", "amount_paid": "Rs. 20", "evidence": {"whether_abuse": "No complaint of abuse", "trial": "certificate has been forwarded", "amount_paid": "Rs. 20 was paid"}},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client, person_name="Ahmad bin Said")
            self.assertEqual(len(result.rows), 2)
            self.assertEqual([person["name"] for person in result.people], ["Fatima bint Ali", "Ahmad bin Said"])
            ahmad = next(person for person in result.people if person["name"] == "Ahmad bin Said")
            self.assertEqual(ahmad["row"]["Trial"], "freedom/manumission outcome")

    def test_run_folder_only_processes_pages_with_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "ocr"
            inter_dir = root / "inter"
            in_dir.mkdir()
            inter_dir.mkdir()
            (in_dir / "p001.txt").write_text(_fixture("kidnapping_abuse.txt"), encoding="utf-8")
            (in_dir / "p002.txt").write_text(_fixture("repatriation.txt"), encoding="utf-8")
            (inter_dir / "p001.classify.json").write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}), encoding="utf-8")
            (inter_dir / "p002.classify.json").write_text(json.dumps({"page": 2, "should_extract": True, "report_type": "correspondence", "evidence": "Official letter regarding Ahmad bin Said"}), encoding="utf-8")
            (inter_dir / "p001.names.json").write_text(json.dumps({"page": 1, "named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]}), encoding="utf-8")
            (inter_dir / "p002.names.json").write_text(json.dumps({"page": 2, "named_people": []}), encoding="utf-8")
            client = FakeClient(
                [
                    {"report_type": "statement", "crime_type": "kidnapping", "whether_abuse": "yes", "conflict_type": None, "trial": "manumission requested", "amount_paid": None, "evidence": {"crime_type": "kidnapped from Zanzibar", "whether_abuse": "beaten severely by her owner", "trial": "requests freedom"}},
                ]
            )
            summary = run_folder(in_dir, inter_dir, inter_dir, client=client, wait_ready=False)
            self.assertEqual(summary["total_pages"], 1)
            self.assertTrue((inter_dir / "p001.meta.json").exists())
            self.assertFalse((inter_dir / "p002.meta.json").exists())


if __name__ == "__main__":
    unittest.main()
