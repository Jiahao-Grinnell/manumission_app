from __future__ import annotations

import io
import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from orchestrator import blueprint as orch_blueprint
from orchestrator import job_store
from orchestrator import name_review
from orchestrator.standalone import create_app


def _doc_paths(root: Path, doc_id: str):
    paths = mock.Mock()
    paths.doc_id = doc_id
    paths.pdf = root / "input_pdfs" / f"{doc_id}.pdf"
    paths.pages_dir = root / "pages" / doc_id
    paths.ocr_dir = root / "ocr_text" / doc_id
    paths.inter_dir = root / "intermediate" / doc_id
    paths.output_dir = root / "output" / doc_id
    paths.logs_dir = root / "logs" / doc_id
    paths.audit_dir = root / "audit" / doc_id
    paths.manifest = lambda: paths.pages_dir / "manifest.json"
    paths.page_image = lambda page: paths.pages_dir / f"p{page:03d}.png"
    paths.ocr_text = lambda page: paths.ocr_dir / f"p{page:03d}.txt"
    paths.classify = lambda page: paths.inter_dir / f"p{page:03d}.classify.json"
    paths.names = lambda page: paths.inter_dir / f"p{page:03d}.names.json"
    paths.meta = lambda page: paths.inter_dir / f"p{page:03d}.meta.json"
    paths.places = lambda page: paths.inter_dir / f"p{page:03d}.places.json"
    return paths


class OrchestratorBlueprintTests(unittest.TestCase):
    def test_run_existing_pdf_creates_job_and_status_can_load_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            fake_settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            (fake_settings.input_pdfs_dir / "sample.pdf").write_bytes(b"%PDF-1.4 fake")

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda doc_id: _doc_paths(root, doc_id)),
                mock.patch.object(orch_blueprint, "_start_worker"),
            ):
                app = create_app()
                client = app.test_client()

                response = client.post(
                    "/orchestrate/run",
                    data={"source_pdf": "sample.pdf", "doc_id": "demo"},
                )

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["doc_id"], "demo")
                self.assertTrue(payload["job_id"])

                status = client.get(f"/orchestrate/status/{payload['job_id']}")
                self.assertEqual(status.status_code, 200)
                status_payload = status.get_json()
                self.assertEqual(status_payload["doc_id"], "demo")
                self.assertEqual(status_payload["status"], "pending")

    def test_pause_endpoint_marks_job_pausing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda doc_id: _doc_paths(root, doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job("demo_pause")
                job["status"] = "running"
                job_store.save_job(job)

                response = client.post(f"/orchestrate/pause/{job['job_id']}")
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "pausing")
                self.assertTrue(payload["pause_requested"])

    def test_clear_results_removes_generated_artifacts_but_keeps_input_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            doc_id = "demo_clear"
            paths = _doc_paths(root, doc_id)
            fake_settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            paths.pdf.write_bytes(b"%PDF-1.4 fake")
            for folder in (paths.pages_dir, paths.ocr_dir, paths.inter_dir, paths.output_dir, paths.logs_dir, paths.audit_dir):
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "marker.txt").write_text("x", encoding="utf-8")

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job(doc_id)
                job["status"] = "done"
                job_store.save_job(job)

                response = client.post(f"/orchestrate/clear-results/{doc_id}")
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "cleared")

            self.assertTrue(paths.pdf.exists())
            self.assertFalse(paths.pages_dir.exists())
            self.assertFalse(paths.ocr_dir.exists())
            self.assertFalse(paths.inter_dir.exists())
            self.assertFalse(paths.output_dir.exists())
            self.assertFalse(paths.logs_dir.exists())
            self.assertFalse(paths.audit_dir.exists())

    def test_outputs_and_download_endpoints_return_final_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            doc_id = "demo_outputs"
            paths = _doc_paths(root, doc_id)
            paths.output_dir.mkdir(parents=True, exist_ok=True)
            (paths.output_dir / "Detailed info.csv").write_text("Name,Page\nMariam,1\n", encoding="utf-8")
            (paths.output_dir / "name place.csv").write_text("Name,Page,Place\nMariam,1,Bushehr\n", encoding="utf-8")
            (paths.output_dir / "run_status.csv").write_text("page,filename,status\n1,p001,ok\n", encoding="utf-8")
            (paths.output_dir / "aggregation_summary.json").write_text(
                '{"doc_id":"demo_outputs","stats":{"detail_rows":1,"place_rows":1},"cleanup_actions":["normalized name"]}',
                encoding="utf-8",
            )

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job(doc_id)
                job["status"] = "done"
                job_store.save_job(job)

                outputs = client.get(f"/orchestrate/outputs/{job['job_id']}")
                self.assertEqual(outputs.status_code, 200)
                payload = outputs.get_json()
                self.assertEqual(payload["doc_id"], doc_id)
                self.assertEqual(payload["files"][0]["row_count"], 1)
                self.assertTrue(payload["summary"]["parse_ok"])

                download = client.get(f"/orchestrate/download/{job['job_id']}/detail")
                self.assertEqual(download.status_code, 200)
                self.assertIn(b"Name,Page", download.data)
                download.close()

    def test_continue_name_review_applies_uploaded_csv_and_restarts_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            doc_id = "demo_review"
            paths = _doc_paths(root, doc_id)
            for folder in (fake_settings.input_pdfs_dir, paths.ocr_dir, paths.inter_dir, paths.output_dir):
                folder.mkdir(parents=True, exist_ok=True)
            paths.pdf.write_bytes(b"%PDF-1.4 fake")
            paths.ocr_text(1).write_text("Mariam and Fatima", encoding="utf-8")
            paths.ocr_text(2).write_text("Salama", encoding="utf-8")
            (paths.inter_dir / "p001.classify.json").write_text(
                '{"page":1,"should_extract":true,"report_type":"statement"}',
                encoding="utf-8",
            )
            (paths.inter_dir / "p002.classify.json").write_text(
                '{"page":2,"should_extract":false,"skip_reason":"index","report_type":"correspondence"}',
                encoding="utf-8",
            )
            (paths.inter_dir / "p001.names.json").write_text(
                '{"page":1,"named_people":[{"name":"Mariam"}]}',
                encoding="utf-8",
            )
            paths.meta(1).write_text('{"stale":true}', encoding="utf-8")
            paths.places(1).write_text('{"stale":true}', encoding="utf-8")
            (paths.output_dir / "Detailed info.csv").write_text("Name,Page\nMariam,1\n", encoding="utf-8")

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
                mock.patch.object(name_review, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
                mock.patch.object(orch_blueprint, "_start_worker") as start_worker,
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job(doc_id)
                job["status"] = "awaiting_name_review"
                job["name_review"] = {"status": "ready"}
                job_store.save_job(job)

                response = client.post(
                    f"/orchestrate/continue-name-review/{job['job_id']}",
                    data={"names_csv": (io.BytesIO(b"Name,Page\nFatima,1\nSalama,2\n"), "corrected.csv")},
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["name_review"]["status"], "uploaded")
                self.assertEqual(payload["name_review"]["name_rows"], 2)
                start_worker.assert_called_once()
                self.assertTrue(start_worker.call_args.args[2]["name_review_completed"])
                self.assertTrue(start_worker.call_args.args[2]["continue_after_name_review"])

            updated_page1 = json.loads(paths.names(1).read_text(encoding="utf-8"))
            updated_page2 = json.loads(paths.names(2).read_text(encoding="utf-8"))
            classify_page2 = json.loads(paths.classify(2).read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in updated_page1["named_people"]], ["Fatima"])
            self.assertEqual([item["name"] for item in updated_page2["named_people"]], ["Salama"])
            self.assertTrue(classify_page2["should_extract"])
            self.assertTrue((paths.output_dir / "name_review_uploaded_names.csv").exists())
            self.assertFalse(paths.meta(1).exists())
            self.assertFalse(paths.places(1).exists())
            self.assertFalse((paths.output_dir / "Detailed info.csv").exists())

    def test_resume_after_reviewed_job_continues_after_name_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            fake_settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            doc_id = "demo_review_resume"
            paths = _doc_paths(root, doc_id)
            paths.pdf.parent.mkdir(parents=True, exist_ok=True)
            paths.pdf.write_bytes(b"%PDF-1.4 fake")

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
                mock.patch.object(orch_blueprint, "_start_worker") as start_worker,
            ):
                app = create_app()
                client = app.test_client()
                current = job_store.create_job(doc_id, source_pdf=paths.pdf.name)
                current["status"] = "paused"
                current["name_review"] = {"status": "uploaded"}
                job_store.save_job(current)

                response = client.post(f"/orchestrate/resume/{doc_id}")

                self.assertEqual(response.status_code, 200)
                start_worker.assert_called_once()
                options = start_worker.call_args.args[2]
                self.assertTrue(options["name_review_completed"])
                self.assertTrue(options["continue_after_name_review"])

    def test_orphaned_running_job_is_marked_paused_on_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda doc_id: _doc_paths(root, doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job("demo_orphan")
                job["status"] = "running"
                job["current_stage"] = "ocr"
                job["updated_at"] = (
                    datetime.now(timezone.utc) - timedelta(seconds=60)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                target = fake_settings.logs_root / "demo_orphan" / "job.json"
                target.write_text(json.dumps(job), encoding="utf-8")

                response = client.get(f"/orchestrate/status/{job['job_id']}")
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "paused")
                self.assertEqual(payload["current_stage"], "")

    def test_index_server_renders_selected_job_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda doc_id: _doc_paths(root, doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job("demo_render")
                job["status"] = "running"
                job["current_stage"] = "ocr"
                job["total_pages"] = 1
                job["log_tail"] = ["[2026-04-24T00:00:00Z] ocr page 001: done."]
                job["pages"] = [
                    {
                        "page": 1,
                        "note": "",
                        "ingest": {"state": "done", "detail": "page image ready"},
                        "ocr": {"state": "running", "detail": "processing ocr"},
                        "classify": {"state": "pending", "detail": ""},
                        "names": {"state": "pending", "detail": ""},
                        "meta": {"state": "pending", "detail": ""},
                        "places": {"state": "pending", "detail": ""},
                        "aggregate": {"state": "pending", "detail": ""},
                    }
                ]
                job_store.save_job(job)

                response = client.get(f"/orchestrate/?job_id={job['job_id']}")
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn("Current Job - demo_render", body)
                self.assertIn("ocr page 001: done.", body)
                self.assertIn("p001", body)
                self.assertIn("state-running", body)

    def test_index_server_renders_name_review_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_settings = mock.Mock(
                DATA_ROOT=root,
                logs_root=root / "logs",
                input_pdfs_dir=root / "input_pdfs",
                OCR_MODEL="ocr-mock",
                OLLAMA_MODEL="text-mock",
            )
            doc_id = "demo_review_render"
            paths = _doc_paths(root, doc_id)
            paths.output_dir.mkdir(parents=True, exist_ok=True)
            (paths.output_dir / "name_review_combined_text.txt").write_text("========== PAGE 001 ==========\nOCR", encoding="utf-8")
            (paths.output_dir / "name_review_names.csv").write_text("Name,Page\nMariam,1\n", encoding="utf-8")

            with (
                mock.patch.object(orch_blueprint, "settings", fake_settings),
                mock.patch.object(job_store, "settings", fake_settings),
                mock.patch.object(orch_blueprint, "doc_paths", side_effect=lambda current_doc_id: _doc_paths(root, current_doc_id)),
            ):
                app = create_app()
                client = app.test_client()
                job = job_store.create_job(doc_id)
                job["status"] = "awaiting_name_review"
                job["name_review"] = {"status": "ready", "message": "Review extracted names."}
                job_store.save_job(job)

                response = client.get(f"/orchestrate/?job_id={job['job_id']}")
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn("Name Review", body)
                self.assertIn("Review extracted names.", body)
                self.assertIn("name_review_combined_text.txt", body)
                self.assertIn("name_review_names.csv", body)
                self.assertNotIn('id="name-review-card" hidden', body)


if __name__ == "__main__":
    unittest.main()
