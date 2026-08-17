from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import fitz

from orchestrator import ingest_ocr


class IngestOcrTests(unittest.TestCase):
    def test_run_ingest_ocr_writes_text_without_persisting_page_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "demo.pdf"
            out_dir = root / "ocr_text" / "demo"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "A small OCR fixture")
            document.save(pdf_path)
            document.close()

            def fake_ocr(image_bgr, out_file, **kwargs):
                self.assertIsNotNone(image_bgr)
                Path(out_file).write_text("A small OCR fixture", encoding="utf-8")
                return SimpleNamespace(
                    elapsed_seconds=0.01,
                    tile_count=1,
                    debug_files=[],
                    text="A small OCR fixture",
                )

            events: list[tuple[str, int, int, str]] = []
            with (
                mock.patch.object(ingest_ocr, "wait_for_ollama_ready"),
                mock.patch.object(ingest_ocr, "ocr_image_bgr", side_effect=fake_ocr),
            ):
                summary = ingest_ocr.run_ingest_ocr(
                    pdf_path,
                    out_dir,
                    dpi=72,
                    doc_id="demo",
                    model="ocr-mock",
                    debug=False,
                    progress=lambda action, page, total, path: events.append((action, page, total, path.name)),
                )

            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["page_count"], 1)
            self.assertEqual((out_dir / "p001.txt").read_text(encoding="utf-8"), "A small OCR fixture")
            self.assertFalse(list(out_dir.glob("*.png")))
            self.assertEqual(events, [("render", 1, 1, "p001.txt"), ("done", 1, 1, "p001.txt")])

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["image_storage"], "not_persisted")
            self.assertEqual(manifest["pages"][0]["status"], "done")

    def test_run_ingest_ocr_stops_between_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "two-pages.pdf"
            out_dir = root / "ocr_text" / "demo"
            document = fitz.open()
            for label in ("first", "second"):
                page = document.new_page()
                page.insert_text((72, 72), label)
            document.save(pdf_path)
            document.close()

            calls: list[str] = []

            def fake_ocr(image_bgr, out_file, **kwargs):
                calls.append(Path(out_file).name)
                Path(out_file).write_text("OCR text", encoding="utf-8")
                return SimpleNamespace(elapsed_seconds=0.01, tile_count=1, debug_files=[], text="OCR text")

            with (
                mock.patch.object(ingest_ocr, "wait_for_ollama_ready"),
                mock.patch.object(ingest_ocr, "ocr_image_bgr", side_effect=fake_ocr),
            ):
                summary = ingest_ocr.run_ingest_ocr(
                    pdf_path,
                    out_dir,
                    dpi=72,
                    debug=False,
                    should_stop=lambda: len(calls) >= 1,
                )

            self.assertTrue(summary["interrupted"])
            self.assertEqual(summary["status"], "interrupted")
            self.assertEqual(calls, ["p001.txt"])
            self.assertTrue((out_dir / "p001.txt").exists())
            self.assertFalse((out_dir / "p002.txt").exists())


if __name__ == "__main__":
    unittest.main()
