from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from modules.name_extractor.core import extract_file, extract_names, rerun_pass_file, run_folder


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
    def test_extract_names_records_model_stage_removals(self) -> None:
        client = FakeClient(
            [
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Sheikh Rashid", "evidence": "sold to one Sheikh Rashid"}]},
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                {"named_people": [{"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
                {"named_people": [{"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
            ]
        )
        result = extract_names(_fixture("single_subject.txt"), report_type="statement", client=client)
        self.assertEqual([item["name"] for item in result.named_people], ["Ahmad bin Said", "Mariam bint Yusuf"])
        self.assertTrue(any(item["name"] == "Sheikh Rashid" and item["stage"] == "pass1_filter" for item in result.removed_candidates))
        self.assertEqual(result.model_calls, 5)

    def test_rule_filter_removes_owner_when_verify_keeps_it(self) -> None:
        client = FakeClient(
            [
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Sheikh Rashid", "evidence": "sold to one Sheikh Rashid"}]},
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Sheikh Rashid", "evidence": "sold to one Sheikh Rashid"}]},
                {"named_people": []},
                {"named_people": []},
                {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Sheikh Rashid", "evidence": "sold to one Sheikh Rashid"}]},
            ]
        )
        result = extract_names(_fixture("owner_vs_slave.txt"), report_type="statement", client=client)
        self.assertEqual([item["name"] for item in result.named_people], ["Mariam bint Yusuf"])
        self.assertTrue(any(item["name"] == "Sheikh Rashid" and item["stage"] == "rule_filter" for item in result.removed_candidates))

    def test_rerun_verify_reuses_existing_upstream_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "ocr"
            inter_dir = root / "intermediate"
            in_dir.mkdir(parents=True)
            inter_dir.mkdir(parents=True)
            (in_dir / "p001.txt").write_text(_fixture("single_subject.txt"), encoding="utf-8")
            (inter_dir / "p001.classify.json").write_text(
                json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}),
                encoding="utf-8",
            )

            first_client = FakeClient(
                [
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                    {"named_people": [{"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
                    {"named_people": [{"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}, {"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"}]},
                ]
            )
            out_path = inter_dir / "p001.names.json"
            first = extract_file(in_dir / "p001.txt", inter_dir / "p001.classify.json", out_path, client=first_client)
            self.assertEqual(len(first.named_people), 2)

            rerun_client = FakeClient(
                [
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                ]
            )
            rerun = rerun_pass_file(
                in_dir / "p001.txt",
                inter_dir / "p001.classify.json",
                out_path,
                "verify",
                client=rerun_client,
            )
            self.assertEqual([item["name"] for item in rerun.named_people], ["Mariam bint Yusuf"])
            self.assertEqual(rerun.model_calls, 1)

    def test_run_folder_uses_only_extractable_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "ocr"
            inter_dir = root / "intermediate"
            in_dir.mkdir(parents=True)
            inter_dir.mkdir(parents=True)
            (in_dir / "p001.txt").write_text(_fixture("single_subject.txt"), encoding="utf-8")
            (in_dir / "p002.txt").write_text(_fixture("grouped_list.txt"), encoding="utf-8")
            (inter_dir / "p001.classify.json").write_text(
                json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}),
                encoding="utf-8",
            )
            (inter_dir / "p002.classify.json").write_text(
                json.dumps({"page": 2, "should_extract": False, "skip_reason": "index", "report_type": "correspondence", "evidence": "Index"}),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                    {"named_people": []},
                    {"named_people": []},
                    {"named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]},
                ]
            )
            summary = run_folder(in_dir, inter_dir, inter_dir, client=client, wait_ready=False)
            self.assertEqual(summary["total_pages"], 1)
            self.assertEqual(summary["completed_pages"], 1)
            self.assertTrue((inter_dir / "p001.names.json").exists())
            self.assertFalse((inter_dir / "p002.names.json").exists())


if __name__ == "__main__":
    unittest.main()
