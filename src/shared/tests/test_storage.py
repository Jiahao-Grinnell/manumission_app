from __future__ import annotations

import csv
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from shared.storage import artifact_ok, read_json, write_csv_atomic, write_json_atomic


class StorageTests(unittest.TestCase):
    def test_atomic_writes_retry_temporary_permission_errors(self) -> None:
        for kind in ("json", "csv"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("checkpoint." + kind)
                path.write_text("original", encoding="utf-8")
                replace = os.replace
                calls = []

                def briefly_locked(source, destination):
                    calls.append(source)
                    self.assertEqual(path.read_text(encoding="utf-8"), "original")
                    if len(calls) < 3:
                        raise PermissionError("destination temporarily locked")
                    replace(source, destination)

                with mock.patch("shared.storage.os.replace", side_effect=briefly_locked), mock.patch("shared.storage.time.sleep"):
                    if kind == "json":
                        write_json_atomic(path, {"page": 306})
                        self.assertEqual(read_json(path), {"page": 306})
                    else:
                        write_csv_atomic(path, [{"page": 306}], ["page"])
                        self.assertIn("306", path.read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 3)
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_permanent_permission_error_preserves_checkpoint_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            write_json_atomic(path, {"page": 305})
            with mock.patch("shared.storage.os.replace", side_effect=PermissionError("locked")) as replace, mock.patch("shared.storage.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_json_atomic(path, {"page": 306})
            self.assertEqual(replace.call_count, 9)
            self.assertEqual(read_json(path), {"page": 305})
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_other_io_errors_are_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            with mock.patch("shared.storage.os.replace", side_effect=OSError("disk failure")) as replace, mock.patch("shared.storage.time.sleep") as sleep:
                with self.assertRaises(OSError):
                    write_json_atomic(path, {})
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

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
