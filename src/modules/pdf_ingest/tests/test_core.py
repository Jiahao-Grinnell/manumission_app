from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import fitz

from modules.pdf_ingest.core import ingest


def _write_tiny_pdf(path: Path) -> None:
    doc = fitz.open()
    try:
        for page_number in range(1, 3):
            page = doc.new_page(width=144, height=144)
            page.insert_text((24, 72), f"Page {page_number}", fontsize=14)
        doc.save(path)
    finally:
        doc.close()


class PdfIngestCoreTests(unittest.TestCase):
    def test_ingest_writes_pngs_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "tiny.pdf"
            out_dir = root / "pages" / "tiny"
            _write_tiny_pdf(pdf_path)

            manifest = ingest(pdf_path, out_dir, dpi=72, doc_id="tiny")

            self.assertEqual(manifest["doc_id"], "tiny")
            self.assertEqual(manifest["page_count"], 2)
            self.assertEqual(manifest["completed_pages"], 2)
            self.assertEqual(manifest["status"], "complete")
            self.assertRegex(manifest["source_pdf_sha256"], re.compile(r"^[0-9a-f]{64}$"))
            self.assertTrue((out_dir / "manifest.json").exists())
            self.assertTrue((out_dir / "p001.png").stat().st_size > 0)
            self.assertTrue((out_dir / "p002.png").stat().st_size > 0)
            self.assertGreater(manifest["pages"][0]["width"], 0)
            self.assertGreater(manifest["pages"][0]["height"], 0)

    def test_partial_manifest_resumes_without_rerendering_done_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "tiny.pdf"
            out_dir = root / "pages" / "tiny"
            _write_tiny_pdf(pdf_path)

            first_events: list[tuple[str, int]] = []
            partial = ingest(
                pdf_path,
                out_dir,
                dpi=72,
                doc_id="tiny",
                end_page=1,
                progress=lambda action, page, total, path: first_events.append((action, page)),
            )
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(first_events, [("render", 1)])

            second_events: list[tuple[str, int]] = []
            resumed = ingest(
                pdf_path,
                out_dir,
                dpi=72,
                doc_id="tiny",
                progress=lambda action, page, total, path: second_events.append((action, page)),
            )

            self.assertEqual(resumed["status"], "complete")
            self.assertEqual(second_events, [("skip", 1), ("render", 2)])


if __name__ == "__main__":
    unittest.main()
