from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import job_store


class JobStoreTests(unittest.TestCase):
    def test_create_save_and_tail_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("demo_doc", source_pdf="demo.pdf", dpi=300, resume=True)
                self.assertTrue((root / "demo_doc" / "job.json").exists())
                self.assertEqual(job["doc_id"], "demo_doc")
                job_store.ensure_pages(job, 2)
                job_store.mark_stage(job, "ocr", 1, state="running", detail="processing ocr")
                job_store.save_job(job)
                job_store.append_log(job, "OCR page 1 running.")

                saved = job_store.load_job("demo_doc")
                self.assertEqual(saved["total_pages"], 2)
                self.assertEqual(saved["pages"][0]["ocr"]["state"], "running")
                self.assertIn("OCR page 1 running.", "\n".join(saved["log_tail"]))
                self.assertTrue((root / "demo_doc" / "events.jsonl").exists())

    def test_load_job_by_id_scans_saved_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                first = job_store.create_job("doc_a")
                second = job_store.create_job("doc_b")
                found = job_store.load_job_by_id(second["job_id"])

                self.assertEqual(found["doc_id"], "doc_b")
                self.assertEqual(job_store.load_job_by_id(first["job_id"])["doc_id"], "doc_a")

    def test_request_pause_marks_job_pausing_and_finalize_clears_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("doc_pause")
                job["status"] = "running"
                job_store.save_job(job)

                paused = job_store.request_pause(job)
                self.assertTrue(paused["pause_requested"])
                self.assertEqual(paused["status"], "pausing")

                job_store.finalize_job(paused, "paused")
                saved = job_store.load_job("doc_pause")
                self.assertEqual(saved["status"], "paused")
                self.assertFalse(saved["pause_requested"])
                self.assertFalse(saved["cancel_requested"])


if __name__ == "__main__":
    unittest.main()
