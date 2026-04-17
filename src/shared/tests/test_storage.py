from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from shared.storage import artifact_ok, read_json, write_csv_atomic, write_json_atomic


class StorageTests(unittest.TestCase):
    def test_write_and_read_json_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "value.json"
            write_json_atomic(path, {"ok": True})
            self.assertEqual(read_json(path), {"ok": True})

    def test_write_csv_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_csv_atomic(path, [{"Name": "A", "Page": 1, "Extra": "ignored"}], ["Name", "Page"])
            with path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows, [{"Name": "A", "Page": "1"}])

    def test_artifact_ok_text_json_and_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = root / "p001.txt"
            text.write_text("[OCR_EMPTY]", encoding="utf-8")
            self.assertTrue(artifact_ok(text, "ocr_text"))

            bad_json = root / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            self.assertFalse(artifact_ok(bad_json, "json"))

            good_json = root / "good.json"
            good_json.write_text('{"page": 1}', encoding="utf-8")
            self.assertTrue(artifact_ok(good_json, "json"))

            image = root / "p001.png"
            image.write_bytes(b"png")
            self.assertTrue(artifact_ok(image, "image"))


if __name__ == "__main__":
    unittest.main()

