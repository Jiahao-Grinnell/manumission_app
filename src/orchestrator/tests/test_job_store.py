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

    def test_load_job_by_id_uses_verified_in_memory_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("doc_indexed")
                with mock.patch.object(job_store, "list_jobs", side_effect=AssertionError("unexpected full scan")):
                    found = job_store.load_job_by_id(job["job_id"])

                self.assertEqual(found["doc_id"], "doc_indexed")

    def test_list_jobs_skips_file_that_disappears_during_stat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job_store.create_job("doc_gone")
                valid = job_store.create_job("doc_valid")
                disappearing = job_store.job_path("doc_gone")
                original_stat = Path.stat
                disappearing_stat_calls = {"count": 0}

                def flaky_stat(path: Path, *args, **kwargs):
                    if path == disappearing:
                        disappearing_stat_calls["count"] += 1
                        if disappearing_stat_calls["count"] >= 2:
                            raise FileNotFoundError(str(path))
                    return original_stat(path, *args, **kwargs)

                with mock.patch.object(Path, "stat", autospec=True, side_effect=flaky_stat):
                    jobs = job_store.list_jobs()

                self.assertEqual([job["job_id"] for job in jobs], [valid["job_id"]])

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

    def test_pending_pause_cannot_be_overwritten_by_stale_worker_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("doc_pending_pause")
                stale_worker = dict(job)

                paused = job_store.request_pause(job)
                stale_worker["status"] = "running"
                stale_worker["current_stage"] = "ocr"
                job_store.save_job(stale_worker)

                saved = job_store.load_job("doc_pending_pause")
                self.assertEqual(paused["status"], "paused")
                self.assertEqual(saved["status"], "paused")
                self.assertEqual(saved["current_stage"], "")
                self.assertFalse(saved["pause_requested"])

    def test_pending_cancel_is_immediate_and_survives_stale_worker_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("doc_pending_cancel")
                stale_worker = dict(job)

                cancelled = job_store.request_cancel(job)
                stale_worker["status"] = "running"
                stale_worker["current_stage"] = "ocr"
                job_store.save_job(stale_worker)

                saved = job_store.load_job("doc_pending_cancel")
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertTrue(cancelled["finished_at"])
                self.assertEqual(saved["status"], "cancelled")
                self.assertEqual(saved["current_stage"], "")
                self.assertFalse(saved["cancel_requested"])

    def test_cancel_replaces_an_inflight_pause_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(logs_root=root)
            with mock.patch.object(job_store, "settings", fake_settings):
                job = job_store.create_job("doc_cancel_pause")
                job["status"] = "running"
                job_store.save_job(job)

                pausing = job_store.request_pause(job)
                cancelling = job_store.request_cancel(pausing)

                self.assertEqual(cancelling["status"], "cancelling")
                self.assertTrue(cancelling["cancel_requested"])
                self.assertFalse(cancelling["pause_requested"])


if __name__ == "__main__":
    unittest.main()
