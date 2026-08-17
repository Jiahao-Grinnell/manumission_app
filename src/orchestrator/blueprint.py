from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, abort, jsonify, render_template, request, send_file, url_for

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id

from . import job_store
from .name_review import apply_corrected_names, clear_uploaded_name_review, name_review_file_map, parse_name_page_csv
from .pipeline import run_document


bp = Blueprint(
    "orchestrator",
    __name__,
    url_prefix="/orchestrate",
    template_folder="templates",
    static_folder="static",
)

_WORKERS: dict[str, threading.Thread] = {}
_CONTROL_LOCKS: dict[str, threading.RLock] = {}
_CONTROL_LOCKS_GUARD = threading.Lock()
_ACTIVE_JOB_STATUSES = {"pending", "running", "cancelling", "pausing"}
_TERMINAL_STREAM_STATUSES = {"done", "done_with_errors", "failed", "cancelled", "paused", "awaiting_name_review"}


@bp.get("/")
def index():
    job_records = [_coerce_orphaned_job(job) for job in job_store.list_jobs()]
    selected_job_id = request.args.get("job_id") or (job_records[0]["job_id"] if job_records else "")
    selected_job = next(
        (job for job in job_records if str(job.get("job_id") or "") == str(selected_job_id)),
        {},
    )
    selected_job_payload = _job_payload(selected_job) if selected_job else {}
    return render_template(
        "dashboard.html",
        jobs=[_job_summary(job) for job in job_records],
        selected_job=selected_job_payload,
        selected_job_id=selected_job_id,
        progress_rows=_stage_progress_rows(selected_job_payload),
        input_pdfs=_list_input_pdfs(),
        asset_version=_asset_version(),
    )


@bp.get("/jobs")
def jobs():
    return jsonify({"jobs": [_job_summary(_coerce_orphaned_job(job)) for job in job_store.list_jobs()]})


@bp.post("/run")
def run():
    payload = request.form if request.form else (request.get_json(silent=True) or {})
    uploaded = request.files.get("pdf")
    source_pdf_name = str(payload.get("source_pdf") or "")
    raw_doc_id = str(payload.get("doc_id") or "").strip()
    dpi = int(payload.get("dpi") or 300)
    resume = str(payload.get("resume", "true")).lower() not in {"0", "false", "no"}
    ocr_model = str(payload.get("ocr_model") or settings.OCR_MODEL)
    text_model = str(payload.get("text_model") or settings.OLLAMA_MODEL)

    uploaded_pdf_path: Path | None = None
    if uploaded and uploaded.filename:
        doc_id = normalize_doc_id(raw_doc_id or Path(uploaded.filename).stem)
        pdf_path = settings.input_pdfs_dir / f"{doc_id}.pdf"
        uploaded_pdf_path = pdf_path
        source_pdf = str(pdf_path)
    elif source_pdf_name:
        source_name = Path(source_pdf_name).name
        source_path = settings.input_pdfs_dir / source_name
        if not source_path.exists():
            abort(404, "Input PDF not found")
        doc_id = normalize_doc_id(raw_doc_id or source_path.stem)
        source_pdf = str(source_path)
    elif raw_doc_id:
        doc_id = normalize_doc_id(raw_doc_id)
        existing_pdf = doc_paths(doc_id).pdf
        source_pdf = str(existing_pdf) if existing_pdf.exists() else ""
    else:
        abort(400, "Provide a PDF upload, an input PDF name, or a doc_id")

    with _control_lock(doc_id):
        current = job_store.latest_job_for_doc(doc_id)
        if current and str(current.get("status") or "") in _ACTIVE_JOB_STATUSES:
            return jsonify(_job_payload(current)), 409

        if uploaded_pdf_path is not None:
            settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
            uploaded.save(uploaded_pdf_path)

        job = job_store.create_job(
            doc_id,
            source_pdf=Path(source_pdf).name if source_pdf else "",
            dpi=dpi,
            resume=resume,
            ocr_model=ocr_model,
            text_model=text_model,
        )
        _start_worker(
            job["job_id"],
            doc_id,
            {
                "source_pdf": source_pdf,
                "dpi": dpi,
                "resume": resume,
                "ocr_model": ocr_model,
                "text_model": text_model,
            },
        )
    return jsonify(_job_payload(job))


@bp.post("/resume/<doc_id>")
def resume(doc_id: str):
    normalized_doc_id = normalize_doc_id(doc_id)
    with _control_lock(normalized_doc_id):
        current = job_store.latest_job_for_doc(normalized_doc_id)
        if current and str(current.get("status") or "") in _ACTIVE_JOB_STATUSES:
            return jsonify(_job_payload(current)), 409
        if current and str(current.get("status") or "") == "awaiting_name_review":
            return jsonify(_job_payload(current)), 409
        review = current.get("name_review") if isinstance(current, dict) else {}
        review_status = str((review if isinstance(review, dict) else {}).get("status") or "")
        continue_after_review = review_status in {"accepted", "uploaded"}
        existing_pdf = doc_paths(normalized_doc_id).pdf
        job = job_store.create_job(
            normalized_doc_id,
            source_pdf=existing_pdf.name if existing_pdf.exists() else "",
            dpi=int((current or {}).get("dpi") or 300),
            resume=True,
            ocr_model=str((current or {}).get("ocr_model") or settings.OCR_MODEL),
            text_model=str((current or {}).get("text_model") or settings.OLLAMA_MODEL),
        )
        options = {
            "source_pdf": str(existing_pdf) if existing_pdf.exists() else "",
            "dpi": int((current or {}).get("dpi") or 300),
            "resume": True,
            "ocr_model": str((current or {}).get("ocr_model") or settings.OCR_MODEL),
            "text_model": str((current or {}).get("text_model") or settings.OLLAMA_MODEL),
        }
        if continue_after_review:
            options["name_review_completed"] = True
            options["continue_after_name_review"] = True
        _start_worker(job["job_id"], normalized_doc_id, options)
    return jsonify(_job_payload(job))


@bp.post("/continue-name-review/<job_id>")
def continue_name_review(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    doc_id = normalize_doc_id(str(current.get("doc_id") or ""))
    with _control_lock(doc_id):
        current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
        if not current:
            abort(404)
        if str(current.get("status") or "") in _ACTIVE_JOB_STATUSES:
            return jsonify(_job_payload(current)), 409

        paths = doc_paths(doc_id)
        uploaded = request.files.get("names_csv")
        review = dict(current.get("name_review") or {})
        if uploaded and uploaded.filename:
            raw = uploaded.read()
            text = raw.decode("utf-8-sig")
            rows = parse_name_page_csv(text)
            applied = apply_corrected_names(doc_id, rows, paths=paths)
            review.update(
                {
                    "status": "uploaded",
                    "reviewed_at": job_store.utc_now(),
                    "uploaded_filename": Path(uploaded.filename).name,
                    "uploaded_csv": applied.get("uploaded_csv") or {},
                    "page_count": int(applied.get("page_count") or review.get("page_count") or 0),
                    "name_rows": int(applied.get("name_rows") or 0),
                    "authoritative": True,
                    "expected_name_page_pairs": int(applied.get("name_rows") or 0),
                    "cleared_downstream": applied.get("cleared_downstream") or {},
                    "message": "Uploaded corrected names CSV is authoritative for all downstream outputs.",
                }
            )
            job_store.append_log(current, f"Applied corrected name review CSV with {review['name_rows']} row(s).")
        else:
            stale_uploaded_removed = clear_uploaded_name_review(doc_id, paths=paths)
            review.pop("uploaded_filename", None)
            review.pop("uploaded_csv", None)
            review.update(
                {
                    "status": "accepted",
                    "reviewed_at": job_store.utc_now(),
                    "authoritative": False,
                    "expected_name_page_pairs": int(review.get("name_rows") or 0),
                    "stale_uploaded_removed": stale_uploaded_removed,
                    "message": "Current extracted names were accepted without changes.",
                }
            )
            job_store.append_log(current, "Accepted current extracted names without corrections.")

        current["name_review"] = review
        current["status"] = "pending"
        current["current_stage"] = ""
        current["finished_at"] = ""
        current["pause_requested"] = False
        current["cancel_requested"] = False
        job_store.save_job(current)
        job_store.emit_event(current, "status", {"status": "pending"})
        _start_worker(
            str(current["job_id"]),
            doc_id,
            {
                "source_pdf": _source_pdf_for_resume(doc_id, current),
                "dpi": int(current.get("dpi") or 300),
                "resume": True,
                "ocr_model": str(current.get("ocr_model") or settings.OCR_MODEL),
                "text_model": str(current.get("text_model") or settings.OLLAMA_MODEL),
                "name_review_completed": True,
                "continue_after_name_review": True,
            },
        )
    return jsonify(_job_payload(current))


@bp.post("/pause/<job_id>")
def pause(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    return jsonify(_job_payload(job_store.request_pause(current)))


@bp.post("/cancel/<job_id>")
def cancel(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    return jsonify(_job_payload(job_store.request_cancel(current)))


@bp.post("/clear-results/<doc_id>")
def clear_results(doc_id: str):
    normalized_doc_id = normalize_doc_id(doc_id)
    with _control_lock(normalized_doc_id):
        current = job_store.latest_job_for_doc(normalized_doc_id)
        if current and str(current.get("status") or "") in _ACTIVE_JOB_STATUSES:
            return jsonify(_job_payload(current)), 409

        paths = doc_paths(normalized_doc_id)
        removed: list[str] = []
        for target in (
            paths.pages_dir,
            paths.ocr_dir,
            paths.inter_dir,
            paths.output_dir,
            paths.logs_dir,
            paths.audit_dir,
        ):
            if _remove_tree_if_exists(target):
                removed.append(str(target))
    return jsonify({"doc_id": normalized_doc_id, "status": "cleared", "removed": removed})


@bp.get("/status/<job_id>")
def status(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    return jsonify(_job_payload(current))


@bp.get("/artifacts/<job_id>/<int:page>")
def artifacts(job_id: str, page: int):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    return jsonify({"job_id": job_id, "doc_id": current["doc_id"], "page": page, "artifacts": _artifact_payload(current["doc_id"], page)})


@bp.get("/log/<job_id>")
def log(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    limit = int(request.args.get("limit", 80))
    return jsonify({"job_id": job_id, "doc_id": current["doc_id"], "lines": job_store.tail_log(current["doc_id"], limit=limit)})


@bp.get("/outputs/<job_id>")
def outputs(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    return jsonify(_output_payload(job_id, current["doc_id"]))


@bp.get("/download/<job_id>/<kind>")
def download_output(job_id: str, kind: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    file_map = _download_file_map(current["doc_id"])
    path = file_map.get(kind)
    if path is None or not path.exists():
        abort(404)
    mimetype = _mimetype_for_path(path)
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=path.name)


@bp.get("/stream/<job_id>")
def stream(job_id: str):
    current = _coerce_orphaned_job(job_store.load_job_by_id(job_id))
    if not current:
        abort(404)
    doc_id = current["doc_id"]
    target = job_store.events_path(doc_id)
    try:
        initial_offset = target.stat().st_size
    except OSError:
        initial_offset = 0

    def generate():
        offset = initial_offset
        yield _sse("snapshot", {"job_id": job_id})
        if str(current.get("status") or "") in _TERMINAL_STREAM_STATUSES:
            return
        while True:
            latest = job_store.load_job(doc_id)
            if not latest or str(latest.get("job_id") or "") != str(job_id):
                break
            if not target.exists():
                if str(latest.get("status") or "") in _TERMINAL_STREAM_STATUSES:
                    break
                time.sleep(1)
                yield ": keep-alive\n\n"
                continue
            try:
                size = target.stat().st_size
                if size < offset:
                    offset = 0
                with target.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except OSError:
                chunk = ""
            if chunk:
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if str(event.get("job_id") or "") != str(job_id):
                        continue
                    yield _sse(str(event.get("event") or "message"), event)
            else:
                yield ": keep-alive\n\n"
                time.sleep(1)
            if str(latest.get("status") or "") in _TERMINAL_STREAM_STATUSES:
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _start_worker(job_id: str, doc_id: str, options: dict[str, Any]) -> None:
    def worker() -> None:
        run_document(job_id, doc_id, options=options)

    thread = threading.Thread(target=worker, daemon=True)
    _WORKERS[job_id] = thread
    thread.start()


def _control_lock(doc_id: str) -> threading.RLock:
    normalized_doc_id = normalize_doc_id(doc_id)
    with _CONTROL_LOCKS_GUARD:
        lock = _CONTROL_LOCKS.get(normalized_doc_id)
        if lock is None:
            lock = threading.RLock()
            _CONTROL_LOCKS[normalized_doc_id] = lock
        return lock


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "job_id",
        "doc_id",
        "status",
        "current_stage",
        "total_pages",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    )
    return {key: job.get(key) for key in keys}


def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job)
    payload["browser_urls"] = _browser_urls()
    payload["pages"] = [_page_payload(payload["doc_id"], page) for page in (job.get("pages") or [])]
    payload["log_tail"] = list(job.get("log_tail") or [])
    payload["name_review"] = _name_review_payload(payload["doc_id"], payload.get("job_id", ""), payload.get("name_review"))
    payload["artifacts_ready"] = {
        "pages_dir": doc_paths(payload["doc_id"]).pages_dir.exists(),
        "ocr_dir": doc_paths(payload["doc_id"]).ocr_dir.exists(),
        "inter_dir": doc_paths(payload["doc_id"]).inter_dir.exists(),
        "output_dir": doc_paths(payload["doc_id"]).output_dir.exists(),
    }
    return payload


def _source_pdf_for_resume(doc_id: str, job: dict[str, Any]) -> str:
    existing_pdf = doc_paths(doc_id).pdf
    if existing_pdf.exists():
        return str(existing_pdf)
    source_pdf = str(job.get("source_pdf") or "")
    candidate = settings.input_pdfs_dir / Path(source_pdf).name
    return str(candidate) if source_pdf and candidate.exists() else ""


def _coerce_orphaned_job(job: dict[str, Any]) -> dict[str, Any]:
    if not job:
        return job
    status = str(job.get("status") or "")
    if status not in _ACTIVE_JOB_STATUSES:
        return job
    job_id = str(job.get("job_id") or "")
    worker = _WORKERS.get(job_id)
    if worker is not None and worker.is_alive():
        return job
    updated_at = _parse_utc(str(job.get("updated_at") or ""))
    if updated_at is None or (time.time() - updated_at) < 15:
        return job
    current = dict(job)
    current["status"] = "paused"
    current["current_stage"] = ""
    current["pause_requested"] = False
    current["cancel_requested"] = False
    errors = list(current.get("errors") or [])
    errors.append("Job was paused because the orchestrator service restarted or lost its worker thread.")
    current["errors"] = errors[-20:]
    tail = list(current.get("log_tail") or [])
    tail.append(f"[{job_store.utc_now()}] Worker thread was missing; job marked paused and can be resumed.")
    current["log_tail"] = tail[-120:]
    job_store.save_job(current)
    job_store.emit_event(current, "status", {"status": "paused"})
    return current


def _page_payload(doc_id: str, page: dict[str, Any]) -> dict[str, Any]:
    item = dict(page)
    item["links"] = _page_links(doc_id, int(page.get("page") or 0))
    return item


def _stage_progress_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    if not job:
        return []
    rows: list[dict[str, Any]] = []
    stage_order = (
        ("ingest", "Ingest"),
        ("ocr", "OCR"),
        ("classify", "Classify"),
        ("names", "Names"),
        ("meta", "Metadata"),
        ("places", "Places"),
        ("aggregate", "Aggregate"),
    )
    for stage_key, label in stage_order:
        counts = _count_stage(job, stage_key)
        total = int(counts["total"] or 0)
        completed = int(counts["done"] or 0) + int(counts["skipped"] or 0)
        percent = int(round((completed / total) * 100)) if total else 0
        rows.append(
            {
                "key": stage_key,
                "label": label,
                "completed": completed,
                "total": total,
                "failed": int(counts["failed"] or 0),
                "percent": percent,
            }
        )
    return rows


def _count_stage(job: dict[str, Any], stage: str) -> dict[str, int]:
    pages = list(job.get("pages") or [])
    if stage == "aggregate":
        aggregate_state = str(((job.get("aggregate") or {}).get("state")) or "pending")
        total = len(pages) or int(job.get("total_pages") or 0)
        return {
            "total": total,
            "done": total if aggregate_state == "done" else 0,
            "running": 1 if aggregate_state == "running" else 0,
            "failed": 1 if aggregate_state == "failed" else 0,
            "skipped": total if aggregate_state == "skipped" else 0,
        }

    counts = {"total": len(pages), "done": 0, "running": 0, "failed": 0, "skipped": 0}
    for page in pages:
        state = str(((page.get(stage) or {}).get("state")) or "pending")
        if state == "done":
            counts["done"] += 1
        elif state == "running":
            counts["running"] += 1
        elif state == "failed":
            counts["failed"] += 1
        elif state == "skipped":
            counts["skipped"] += 1
    return counts


def _list_input_pdfs() -> list[dict[str, Any]]:
    root = settings.input_pdfs_dir
    if not root.exists():
        return []
    return [
        {"name": path.name, "size_bytes": path.stat().st_size}
        for path in sorted(root.glob("*.pdf"), key=lambda item: item.name.lower())
    ]


def _asset_version() -> str:
    candidates = [
        Path(bp.root_path) / "static" / "dashboard.js",
        Path(bp.root_path) / "static" / "dashboard.css",
        Path(bp.root_path) / "templates" / "dashboard.html",
    ]
    mtimes = [int(path.stat().st_mtime) for path in candidates if path.exists()]
    return str(max(mtimes)) if mtimes else str(int(time.time()))


def _browser_urls() -> dict[str, str]:
    return {
        "ingest": "http://127.0.0.1:5102",
        "ocr": "http://127.0.0.1:5103",
        "classify": "http://127.0.0.1:5104",
        "names": "http://127.0.0.1:5105",
        "meta": "http://127.0.0.1:5106",
        "places": "http://127.0.0.1:5107",
        "aggregate": "http://127.0.0.1:5109",
        "orchestrate": "http://127.0.0.1:5110",
    }


def _page_links(doc_id: str, page: int) -> dict[str, str]:
    encoded_doc = quote(doc_id)
    return {
        "ingest": f"http://127.0.0.1:5102/ingest/?doc_id={encoded_doc}",
        "ocr": f"http://127.0.0.1:5103/ocr/?doc_id={encoded_doc}&page={page}",
        "classify": f"http://127.0.0.1:5104/classify/?doc_id={encoded_doc}&page={page}",
        "names": f"http://127.0.0.1:5105/names/?doc_id={encoded_doc}&page={page}",
        "meta": f"http://127.0.0.1:5106/meta/?doc_id={encoded_doc}&page={page}",
        "places": f"http://127.0.0.1:5107/places/?doc_id={encoded_doc}&page={page}",
        "aggregate": f"http://127.0.0.1:5109/aggregate/?doc_id={encoded_doc}",
    }


def _artifact_payload(doc_id: str, page: int) -> dict[str, Any]:
    paths = doc_paths(doc_id)
    artifacts = {
        "page_image": paths.page_image(page),
        "ocr_text": paths.ocr_text(page),
        "classify": paths.classify(page),
        "names": paths.names(page),
        "meta": paths.meta(page),
        "places": paths.places(page),
    }
    return {key: _file_status(path) for key, path in artifacts.items()}


def _output_payload(job_id: str, doc_id: str) -> dict[str, Any]:
    output_dir = doc_paths(doc_id).output_dir
    files: list[dict[str, Any]] = []
    for kind, path in _output_file_map(doc_id).items():
        preview = _csv_preview(path) if path.suffix.lower() == ".csv" else _json_preview(path)
        files.append(
            {
                "key": kind,
                "label": path.name,
                "download_url": url_for("orchestrator.download_output", job_id=job_id, kind=kind),
                **preview,
            }
        )
    summary = _json_preview(output_dir / "aggregation_summary.json")
    return {"job_id": job_id, "doc_id": doc_id, "files": files, "summary": summary}


def _output_file_map(doc_id: str) -> dict[str, Path]:
    output_dir = doc_paths(doc_id).output_dir
    return {
        "detail": output_dir / "Detailed info.csv",
        "places": output_dir / "name place.csv",
        "status": output_dir / "run_status.csv",
    }


def _mimetype_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".txt":
        return "text/plain"
    return "text/csv"


def _download_file_map(doc_id: str) -> dict[str, Path]:
    return {
        **_output_file_map(doc_id),
        **name_review_file_map(doc_id, paths=doc_paths(doc_id)),
    }


def _name_review_payload(doc_id: str, job_id: str, raw_review: Any) -> dict[str, Any]:
    review = dict(raw_review) if isinstance(raw_review, dict) else {}
    files: list[dict[str, Any]] = []
    labels = {
        "name_review_text": "Combined Text",
        "name_review_csv": "Current Names CSV",
        "name_review_uploaded_csv": "Uploaded Correction CSV",
    }
    for key, path in name_review_file_map(doc_id, paths=doc_paths(doc_id)).items():
        status = _base_preview(path)
        if not status["exists"]:
            continue
        files.append(
            {
                "key": key,
                "label": labels.get(key, path.name),
                "download_url": url_for("orchestrator.download_output", job_id=job_id, kind=key) if job_id else "",
                **status,
            }
        )
    review["files"] = files
    return review


def _file_status(path: Path) -> dict[str, Any]:
    exists = path.exists()
    status = {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)) if exists else "",
        "parse_ok": False,
    }
    if not exists:
        return status
    if path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            status["parse_ok"] = False
        else:
            status["parse_ok"] = True
    else:
        status["parse_ok"] = path.stat().st_size > 0
    return status


def _csv_preview(path: Path, *, limit: int = 8) -> dict[str, Any]:
    status = _base_preview(path)
    if not status["exists"]:
        status.update({"headers": [], "rows": [], "row_count": 0, "preview_truncated": False})
        return status

    headers: list[str] = []
    rows: list[dict[str, str]] = []
    row_count = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = list(reader.fieldnames or [])
            for row in reader:
                row_count += 1
                if len(rows) < limit:
                    rows.append({key: str((row or {}).get(key) or "") for key in headers})
    except Exception as exc:
        status["parse_ok"] = False
        status["error"] = str(exc)
        status.update({"headers": headers, "rows": [], "row_count": 0, "preview_truncated": False})
        return status

    status["parse_ok"] = True
    status.update(
        {
            "headers": headers,
            "rows": rows,
            "row_count": row_count,
            "preview_truncated": row_count > len(rows),
        }
    )
    return status


def _json_preview(path: Path) -> dict[str, Any]:
    status = _base_preview(path)
    if not status["exists"]:
        status["data"] = {}
        return status
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        status["parse_ok"] = False
        status["error"] = str(exc)
        status["data"] = {}
        return status
    status["parse_ok"] = True
    status["data"] = data if isinstance(data, dict) else {"value": data}
    return status


def _base_preview(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)) if exists else "",
        "parse_ok": False,
        "error": "",
    }


def _remove_tree_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    resolved = path.resolve()
    data_root = Path(settings.DATA_ROOT).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to delete path outside DATA_ROOT: {resolved}") from exc
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def _parse_utc(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
