from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from modules.page_classifier.core import classify, run_folder


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
    def test_classify_statement_fixture_applies_override(self) -> None:
        client = FakeClient(
            [{"should_extract": True, "skip_reason": None, "report_type": "correspondence", "evidence": "Statement of slave Mariam bint Yusuf"}]
        )
        result = classify(_fixture("statement_page.txt"), client=client)
        self.assertTrue(result.should_extract)
        self.assertEqual(result.report_type, "statement")
        self.assertTrue(result.override["applied"])

    def test_classify_transport_fixture_applies_override(self) -> None:
        client = FakeClient(
            [{"should_extract": True, "skip_reason": None, "report_type": "correspondence", "evidence": "grant him the usual manumission certificate"}]
        )
        result = classify(_fixture("transport_page.txt"), client=client)
        self.assertEqual(result.report_type, "correspondence")

    def test_classify_correspondence_fixture_keeps_model_decision(self) -> None:
        client = FakeClient(
            [{"should_extract": True, "skip_reason": None, "report_type": "correspondence", "evidence": "for the information of the Government of India"}]
        )
        result = classify(_fixture("correspondence_page.txt"), client=client)
        self.assertEqual(result.report_type, "correspondence")
        self.assertFalse(result.override["applied"])

    def test_classify_index_fixture_keeps_skip_reason(self) -> None:
        client = FakeClient(
            [{"should_extract": False, "skip_reason": "index", "report_type": "correspondence", "evidence": "Index of papers relating to slavery in Kuwait"}]
        )
        result = classify(_fixture("index_page.txt"), client=client)
        self.assertFalse(result.should_extract)
        self.assertEqual(result.skip_reason, "index")

    def test_classify_bad_ocr_fixture_keeps_skip_reason(self) -> None:
        client = FakeClient(
            [{"should_extract": False, "skip_reason": "bad_ocr", "report_type": "correspondence", "evidence": "[OCR_EMPTY]"}]
        )
        result = classify(_fixture("bad_ocr_page.txt"), client=client)
        self.assertFalse(result.should_extract)
        self.assertEqual(result.skip_reason, "bad_ocr")

    def test_run_folder_skips_existing_results_when_resume_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "ocr"
            out_dir = root / "intermediate"
            in_dir.mkdir(parents=True)
            (in_dir / "p001.txt").write_text(_fixture("statement_page.txt"), encoding="utf-8")
            (in_dir / "p002.txt").write_text(_fixture("transport_page.txt"), encoding="utf-8")
            out_dir.mkdir(parents=True)
            (out_dir / "p001.classify.json").write_text(json.dumps({"page": 1, "report_type": "statement", "should_extract": True}), encoding="utf-8")

            client = FakeClient(
                [{"should_extract": True, "skip_reason": None, "report_type": "correspondence", "evidence": "manumission certificate"}]
            )
            summary = run_folder(in_dir, out_dir, client=client, wait_ready=False, resume=True)

            self.assertEqual(summary["skipped_pages"], 1)
            self.assertEqual(summary["completed_pages"], 1)
            self.assertEqual(summary["error_pages"], 0)
            self.assertTrue((out_dir / "p002.classify.json").exists())


if __name__ == "__main__":
    unittest.main()
