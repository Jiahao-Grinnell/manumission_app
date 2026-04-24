from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shared.storage import write_json_atomic
from orchestrator import job_store, pipeline


def _stage_summary(stage: str, pages: list[tuple[int, str]]) -> dict:
    return {
        "status": "complete",
        "pages": [
            {
                "page": page,
                "status": status,
                "filename": f"p{page:03d}.{stage}.json",
            }
            for page, status in pages
        ],
    }


class PipelineMockedTests(unittest.TestCase):
    def test_pipeline_happy_path_with_skip_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            fake_settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            paths = mock.Mock()
            paths.doc_id = "demo"
            paths.pdf = fake_settings.input_pdfs_dir / "demo.pdf"
            paths.pages_dir = root / "pages" / "demo"
            paths.ocr_dir = root / "ocr_text" / "demo"
            paths.inter_dir = root / "intermediate" / "demo"
            paths.output_dir = root / "output" / "demo"
            paths.manifest = lambda: paths.pages_dir / "manifest.json"
            paths.page_image = lambda page: paths.pages_dir / f"p{page:03d}.png"
            paths.ocr_text = lambda page: paths.ocr_dir / f"p{page:03d}.txt"
            paths.classify = lambda page: paths.inter_dir / f"p{page:03d}.classify.json"
            paths.names = lambda page: paths.inter_dir / f"p{page:03d}.names.json"
            paths.meta = lambda page: paths.inter_dir / f"p{page:03d}.meta.json"
            paths.places = lambda page: paths.inter_dir / f"p{page:03d}.places.json"
            for folder in (paths.pages_dir, paths.ocr_dir, paths.inter_dir, paths.output_dir, fake_settings.logs_root):
                folder.mkdir(parents=True, exist_ok=True)
            paths.pdf.write_bytes(b"%PDF-1.4 fake")

            def fake_run_stage(stage, doc_id, **kwargs):
                if stage == "ingest":
                    for page in (1, 2):
                        image = paths.page_image(page)
                        image.write_bytes(b"png")
                        kwargs["progress"]("render", page, 2, image)
                    manifest = {"doc_id": doc_id, "page_count": 2, "completed_pages": 2, "status": "complete"}
                    write_json_atomic(paths.manifest(), manifest)
                    return manifest
                if stage == "ocr":
                    for page in (1, 2):
                        text = paths.ocr_text(page)
                        text.write_text(f"ocr {page}", encoding="utf-8")
                        kwargs["progress"]("done", page, 2, text)
                    return _stage_summary("txt", [(1, "done"), (2, "done")])
                if stage == "classify":
                    write_json_atomic(paths.classify(1), {"page": 1, "should_extract": True, "report_type": "statement"})
                    write_json_atomic(paths.classify(2), {"page": 2, "should_extract": False, "skip_reason": "index", "report_type": "correspondence"})
                    kwargs["progress"]("done", 1, 2, paths.ocr_text(1))
                    kwargs["progress"]("done", 2, 2, paths.ocr_text(2))
                    return _stage_summary("classify", [(1, "done"), (2, "done")])
                if stage == "names":
                    write_json_atomic(paths.names(1), {"page": 1, "named_people": [{"name": "Mariam"}]})
                    kwargs["progress"]("done", 1, 1, paths.ocr_text(1))
                    return _stage_summary("names", [(1, "done")])
                if stage == "meta":
                    write_json_atomic(paths.meta(1), {"page": 1, "names": ["Mariam"], "rows": [{"Name": "Mariam", "Page": 1}]})
                    kwargs["progress"]("done", 1, 1, paths.ocr_text(1))
                    return _stage_summary("meta", [(1, "done")])
                if stage == "places":
                    write_json_atomic(paths.places(1), {"page": 1, "names": ["Mariam"], "rows": [{"Name": "Mariam", "Page": 1, "Place": "Bushehr"}]})
                    kwargs["progress"]("done", 1, 1, paths.ocr_text(1))
                    return _stage_summary("places", [(1, "done")])
                if stage == "aggregate":
                    (paths.output_dir / "Detailed info.csv").write_text("Name,Page\nMariam,1\n", encoding="utf-8")
                    (paths.output_dir / "name place.csv").write_text("Name,Page,Place\nMariam,1,Bushehr\n", encoding="utf-8")
                    (paths.output_dir / "run_status.csv").write_text("page,filename,status\n1,p001,ok\n", encoding="utf-8")
                    return {"status": "complete"}
                raise AssertionError(stage)

            with (
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(pipeline, "settings", fake_settings),
                mock.patch.object(pipeline, "doc_paths", return_value=paths),
                mock.patch.object(pipeline, "run_stage", side_effect=fake_run_stage),
            ):
                job = job_store.create_job("demo", source_pdf="demo.pdf", ocr_model="ocr-mock", text_model="text-mock")
                result = pipeline.run_document(job["job_id"], "demo", options={"source_pdf": str(paths.pdf), "resume": True})

            self.assertEqual(result["status"], "done")
            self.assertEqual(result["pages"][0]["names"]["state"], "done")
            self.assertEqual(result["pages"][0]["meta"]["state"], "done")
            self.assertEqual(result["pages"][0]["places"]["state"], "done")
            self.assertEqual(result["pages"][1]["names"]["state"], "skipped")
            self.assertEqual(result["pages"][1]["meta"]["state"], "skipped")
            self.assertEqual(result["pages"][1]["places"]["state"], "skipped")
            self.assertEqual(result["pages"][1]["note"], "skip:index")
            self.assertEqual(result["aggregate"]["state"], "done")

    def test_pipeline_pause_requested_stops_after_current_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            fake_settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            paths = mock.Mock()
            paths.doc_id = "demo_pause"
            paths.pdf = fake_settings.input_pdfs_dir / "demo_pause.pdf"
            paths.pages_dir = root / "pages" / "demo_pause"
            paths.ocr_dir = root / "ocr_text" / "demo_pause"
            paths.inter_dir = root / "intermediate" / "demo_pause"
            paths.output_dir = root / "output" / "demo_pause"
            paths.manifest = lambda: paths.pages_dir / "manifest.json"
            paths.page_image = lambda page: paths.pages_dir / f"p{page:03d}.png"
            paths.ocr_text = lambda page: paths.ocr_dir / f"p{page:03d}.txt"
            paths.classify = lambda page: paths.inter_dir / f"p{page:03d}.classify.json"
            paths.names = lambda page: paths.inter_dir / f"p{page:03d}.names.json"
            paths.meta = lambda page: paths.inter_dir / f"p{page:03d}.meta.json"
            paths.places = lambda page: paths.inter_dir / f"p{page:03d}.places.json"
            for folder in (paths.pages_dir, paths.ocr_dir, paths.inter_dir, paths.output_dir, fake_settings.logs_root):
                folder.mkdir(parents=True, exist_ok=True)
            paths.pdf.write_bytes(b"%PDF-1.4 fake")

            def fake_run_stage(stage, doc_id, **kwargs):
                if stage == "ingest":
                    for page in (1, 2):
                        image = paths.page_image(page)
                        image.write_bytes(b"png")
                        kwargs["progress"]("render", page, 2, image)
                    manifest = {"doc_id": doc_id, "page_count": 2, "completed_pages": 2, "status": "complete"}
                    write_json_atomic(paths.manifest(), manifest)
                    return manifest
                if stage == "ocr":
                    for page in (1, 2):
                        text = paths.ocr_text(page)
                        text.write_text(f"ocr {page}", encoding="utf-8")
                        kwargs["progress"]("done", page, 2, text)
                        if page == 1:
                            current = job_store.load_job("demo_pause")
                            job_store.request_pause(current)
                    return _stage_summary("txt", [(1, "done"), (2, "done")])
                raise AssertionError(stage)

            with (
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(pipeline, "settings", fake_settings),
                mock.patch.object(pipeline, "doc_paths", return_value=paths),
                mock.patch.object(pipeline, "run_stage", side_effect=fake_run_stage),
            ):
                job = job_store.create_job("demo_pause", source_pdf="demo_pause.pdf", ocr_model="ocr-mock", text_model="text-mock")
                result = pipeline.run_document(job["job_id"], "demo_pause", options={"source_pdf": str(paths.pdf), "resume": True})

            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["pages"][0]["ocr"]["state"], "done")
            self.assertEqual(result["pages"][1]["ocr"]["state"], "done")
            self.assertEqual(result["pages"][0]["classify"]["state"], "pending")
            self.assertEqual(result["aggregate"]["state"], "pending")


if __name__ == "__main__":
    unittest.main()
