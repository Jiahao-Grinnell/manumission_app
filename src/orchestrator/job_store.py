from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.config import settings
from shared.paths import normalize_doc_id
from shared.storage import write_json_atomic


_LOCK = threading.Lock()
_TAIL_LIMIT = 120
PAGE_STAGE_KEYS = ("ingest", "ocr", "classify", "names", "meta", "places", "aggregate")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stage_record(state: str = "pending") -> dict[str, Any]:
    return {
        "state": state,
        "started_at": "",
        "finished_at": "",
        "elapsed_seconds": 0.0,
        "error": "",
        "detail": "",
    }


def page_record(page: int) -> dict[str, Any]:
    return {
        "page": int(page),
        "note": "",
        **{key: stage_record() for key in PAGE_STAGE_KEYS},
    }


def doc_log_dir(doc_id: str) -> Path:
    return settings.logs_root / normalize_doc_id(doc_id)


def job_path(doc_id: str) -> Path:
    return doc_log_dir(doc_id) / "job.json"


def log_path(doc_id: str) -> Path:
    return doc_log_dir(doc_id) / "pipeline.log"


def events_path(doc_id: str) -> Path:
    return doc_log_dir(doc_id) / "events.jsonl"


def create_job(
    doc_id: str,
    *,
    source_pdf: str = "",
    dpi: int = 300,
    resume: bool = True,
    ocr_model: str = "",
    text_model: str = "",
) -> dict[str, Any]:
    normalized_doc_id = normalize_doc_id(doc_id)
    now = utc_now()
    job = {
        "job_id": uuid.uuid4().hex[:12],
        "doc_id": normalized_doc_id,
        "status": "pending",
        "current_stage": "",
        "created_at": now,
        "updated_at": now,
        "started_at": "",
        "finished_at": "",
        "source_pdf": source_pdf,
        "dpi": int(dpi),
        "resume": bool(resume),
        "ocr_model": ocr_model,
        "text_model": text_model,
        "total_pages": 0,
        "pages": [],
        "aggregate": stage_record(),
        "errors": [],
        "log_tail": [],
        "cancel_requested": False,
        "pause_requested": False,
    }
    save_job(job, reset_logs=True)
    emit_event(job, "created", {"status": job["status"]})
    return job


def save_job(job: dict[str, Any], *, reset_logs: bool = False) -> None:
    normalized_doc_id = normalize_doc_id(str(job.get("doc_id") or ""))
    target = job_path(normalized_doc_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(job)
    payload["doc_id"] = normalized_doc_id
    payload["updated_at"] = utc_now()
    existing: dict[str, Any] = {}
    if target.exists() and not reset_logs:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8-sig"))
        except Exception:
            loaded = {}
        existing = loaded if isinstance(loaded, dict) else {}
    active_statuses = {"pending", "running", "pausing", "cancelling"}
    if existing and str(payload.get("status") or "") in active_statuses:
        if existing.get("cancel_requested") and not payload.get("pause_requested"):
            payload["cancel_requested"] = True
            if str(payload.get("status") or "") == "running":
                payload["status"] = "cancelling"
        if existing.get("pause_requested") and not payload.get("cancel_requested"):
            payload["pause_requested"] = True
            if str(payload.get("status") or "") == "running":
                payload["status"] = "pausing"
    if reset_logs:
        log_path(normalized_doc_id).write_text("", encoding="utf-8")
        events_path(normalized_doc_id).write_text("", encoding="utf-8")
    with _LOCK:
        write_json_atomic(target, payload)
    job.clear()
    job.update(payload)


def load_job(doc_id: str) -> dict[str, Any]:
    target = job_path(doc_id)
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def list_jobs() -> list[dict[str, Any]]:
    root = settings.logs_root
    if not root.exists():
        return []
    jobs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/job.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        data = load_job(path.parent.name)
        if data:
            jobs.append(data)
    return jobs


def load_job_by_id(job_id: str) -> dict[str, Any]:
    for job in list_jobs():
        if str(job.get("job_id") or "") == str(job_id):
            return job
    return {}


def ensure_pages(job: dict[str, Any], total_pages: int) -> None:
    if total_pages < 0:
        total_pages = 0
    pages = [page_record(page) for page in range(1, total_pages + 1)]
    existing = {int(item.get("page", 0)): item for item in (job.get("pages") or []) if isinstance(item, dict)}
    merged: list[dict[str, Any]] = []
    for page in range(1, total_pages + 1):
        current = page_record(page)
        previous = existing.get(page)
        if previous:
            current.update({"note": str(previous.get("note") or "")})
            for key in PAGE_STAGE_KEYS:
                value = previous.get(key)
                if isinstance(value, dict):
                    current[key].update(value)
        merged.append(current)
    job["pages"] = merged
    job["total_pages"] = total_pages


def get_page(job: dict[str, Any], page: int) -> dict[str, Any]:
    ensure_pages(job, max(int(job.get("total_pages") or 0), int(page)))
    return job["pages"][int(page) - 1]


def mark_stage(
    job: dict[str, Any],
    stage: str,
    page: int,
    *,
    state: str,
    detail: str = "",
    error: str = "",
) -> None:
    record = get_page(job, page)[stage]
    now = utc_now()
    if state == "running" and not record.get("started_at"):
        record["started_at"] = now
    if state in {"done", "skipped", "failed"}:
        if not record.get("started_at"):
            record["started_at"] = now
        record["finished_at"] = now
        record["elapsed_seconds"] = _elapsed_seconds(record.get("started_at"), now)
    record["state"] = state
    if detail:
        record["detail"] = str(detail)
    if error:
        record["error"] = str(error)


def mark_doc_stage(job: dict[str, Any], stage: str, state: str, *, detail: str = "", error: str = "") -> None:
    record = job["aggregate"] if stage == "aggregate" else None
    if record is None:
        return
    now = utc_now()
    if state == "running" and not record.get("started_at"):
        record["started_at"] = now
    if state in {"done", "skipped", "failed"}:
        if not record.get("started_at"):
            record["started_at"] = now
        record["finished_at"] = now
        record["elapsed_seconds"] = _elapsed_seconds(record.get("started_at"), now)
    record["state"] = state
    if detail:
        record["detail"] = str(detail)
    if error:
        record["error"] = str(error)


def append_log(job: dict[str, Any], message: str) -> None:
    normalized_doc_id = normalize_doc_id(str(job.get("doc_id") or ""))
    stamped = f"[{utc_now()}] {message}"
    path = log_path(normalized_doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(stamped + "\n")
    tail = deque(job.get("log_tail") or [], maxlen=_TAIL_LIMIT)
    tail.append(stamped)
    job["log_tail"] = list(tail)
    save_job(job)
    emit_event(job, "log", {"message": stamped})


def emit_event(job: dict[str, Any], event: str, payload: dict[str, Any] | None = None) -> None:
    normalized_doc_id = normalize_doc_id(str(job.get("doc_id") or ""))
    target = events_path(normalized_doc_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "ts": utc_now(),
        "event": event,
        "job_id": str(job.get("job_id") or ""),
        "doc_id": normalized_doc_id,
    }
    if payload:
        item.update(payload)
    line = json.dumps(item, ensure_ascii=False)
    with _LOCK:
        with target.open("a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")


def finalize_job(job: dict[str, Any], status: str, *, error: str = "") -> None:
    job["status"] = status
    job["current_stage"] = ""
    job["finished_at"] = utc_now()
    job["cancel_requested"] = False
    job["pause_requested"] = False
    if error:
        errors = list(job.get("errors") or [])
        errors.append(error)
        job["errors"] = errors[-20:]
    save_job(job)
    emit_event(job, "done", {"status": status, "error": error})


def request_cancel(job: dict[str, Any]) -> dict[str, Any]:
    current = dict(job)
    current["cancel_requested"] = True
    current["pause_requested"] = False
    if current.get("status") == "running":
        current["status"] = "cancelling"
    save_job(current)
    emit_event(current, "cancel_requested", {"status": current["status"]})
    return current


def request_pause(job: dict[str, Any]) -> dict[str, Any]:
    current = dict(job)
    current["pause_requested"] = True
    current["cancel_requested"] = False
    if current.get("status") == "running":
        current["status"] = "pausing"
    elif current.get("status") == "pending":
        current["status"] = "paused"
        current["pause_requested"] = False
        current["finished_at"] = utc_now()
    save_job(current)
    emit_event(current, "pause_requested", {"status": current["status"]})
    return current


def tail_log(doc_id: str, *, limit: int = 60) -> list[str]:
    target = log_path(doc_id)
    if not target.exists():
        return []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max(1, int(limit)) :]


def latest_job_for_doc(doc_id: str) -> dict[str, Any]:
    return load_job(doc_id)


def _elapsed_seconds(started_at: str, finished_at: str) -> float:
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except Exception:
        return 0.0
    return round(max(0.0, (finished - started).total_seconds()), 2)
