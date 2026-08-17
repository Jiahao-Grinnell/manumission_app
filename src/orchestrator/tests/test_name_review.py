from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orchestrator.name_review import apply_corrected_names, generate_name_review_artifacts


def _paths(root: Path, doc_id: str = "review_doc") -> SimpleNamespace:
    ocr_dir = root / "ocr_text" / doc_id
    inter_dir = root / "intermediate" / doc_id
    output_dir = root / "output" / doc_id
    for directory in (ocr_dir, inter_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        doc_id=doc_id,
        ocr_dir=ocr_dir,
        inter_dir=inter_dir,
        output_dir=output_dir,
        ocr_text=lambda page: ocr_dir / f"p{page:03d}.txt",
        classify=lambda page: inter_dir / f"p{page:03d}.classify.json",
        names=lambda page: inter_dir / f"p{page:03d}.names.json",
        meta=lambda page: inter_dir / f"p{page:03d}.meta.json",
        places=lambda page: inter_dir / f"p{page:03d}.places.json",
    )


class NameReviewTests(unittest.TestCase):
    def test_apply_preserves_case_distinct_names_and_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            paths.ocr_text(1).write_text("Amnah made a statement.", encoding="utf-8")
            paths.classify(1).write_text(
                json.dumps({"page": 1, "should_extract": True, "report_type": "statement"}),
                encoding="utf-8",
            )
            paths.names(1).write_text(json.dumps({"page": 1, "named_people": []}), encoding="utf-8")

            result = apply_corrected_names(
                paths.doc_id,
                [
                    {"Name": "AMNAH", "Page": 1},
                    {"Name": "Amnah", "Page": 1},
                    {"Name": "AMNAH", "Page": 1},
                ],
                paths=paths,
            )

            self.assertEqual(result["name_rows"], 2)
            names_record = json.loads(paths.names(1).read_text(encoding="utf-8"))
            self.assertEqual([person["name"] for person in names_record["named_people"]], ["AMNAH", "Amnah"])
            with (paths.output_dir / "name_review_uploaded_names.csv").open(encoding="utf-8", newline="") as fh:
                uploaded = list(csv.DictReader(fh))
            self.assertEqual(uploaded, [{"Name": "AMNAH", "Page": "1"}, {"Name": "Amnah", "Page": "1"}])

    def test_generate_new_checkpoint_removes_stale_uploaded_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            paths.ocr_text(1).write_text("Mariam made a statement.", encoding="utf-8")
            paths.names(1).write_text(
                json.dumps({"page": 1, "named_people": [{"name": "Mariam"}]}),
                encoding="utf-8",
            )
            stale = paths.output_dir / "name_review_uploaded_names.csv"
            stale.write_text("Name,Page\nOld Name,9\n", encoding="utf-8")

            result = generate_name_review_artifacts(paths.doc_id, paths=paths)

            self.assertTrue(result["stale_uploaded_removed"])
            self.assertFalse(stale.exists())
            self.assertTrue((paths.output_dir / "name_review_names.csv").exists())

    def test_invalid_page_preflight_leaves_all_artifacts_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            paths.ocr_text(1).write_text("Mariam made a statement.", encoding="utf-8")
            original_classify = '{"page":1,"should_extract":true,"report_type":"statement"}'
            original_names = '{"page":1,"named_people":[{"name":"Mariam"}]}'
            paths.classify(1).write_text(original_classify, encoding="utf-8")
            paths.names(1).write_text(original_names, encoding="utf-8")
            paths.meta(1).write_text('{"keep":true}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "OCR text is missing"):
                apply_corrected_names(
                    paths.doc_id,
                    [{"Name": "Fatima", "Page": 1}, {"Name": "Salama", "Page": 2}],
                    paths=paths,
                )

            self.assertEqual(paths.classify(1).read_text(encoding="utf-8"), original_classify)
            self.assertEqual(paths.names(1).read_text(encoding="utf-8"), original_names)
            self.assertTrue(paths.meta(1).exists())
            self.assertFalse((paths.output_dir / "name_review_uploaded_names.csv").exists())

    def test_invalid_classify_preflight_leaves_all_artifacts_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            paths.ocr_text(1).write_text("Mariam made a statement.", encoding="utf-8")
            paths.classify(1).write_text("not-json", encoding="utf-8")
            original_names = '{"page":1,"named_people":[{"name":"Mariam"}]}'
            paths.names(1).write_text(original_names, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "classify artifact"):
                apply_corrected_names(paths.doc_id, [{"Name": "Fatima", "Page": 1}], paths=paths)

            self.assertEqual(paths.classify(1).read_text(encoding="utf-8"), "not-json")
            self.assertEqual(paths.names(1).read_text(encoding="utf-8"), original_names)
            self.assertFalse((paths.output_dir / "name_review_uploaded_names.csv").exists())


if __name__ == "__main__":
    unittest.main()
