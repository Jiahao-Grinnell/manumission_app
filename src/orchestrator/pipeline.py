from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id
from shared.storage import artifact_ok, read_json

from . import job_store
from .router import run_stage


PipelineOptions = dict[str, Any]


def run_document(job_id: str, doc_id: str, *, options: PipelineOptions | None = None) -> dict[str, Any]:
    normalized_doc_id = normalize_doc_id(doc_id)
    paths = doc_paths(normalized_doc_id)
    opts = dict(options or {})
    source_pdf = _resolve_pdf_source(normalized_doc_id, opts.get("source_pdf"))
    job = job_store.load_job(normalized_doc_id)
    if not job or str(job.get("job_id") or "") != str(job_id):
        job = job_store.create_job(
            normalized_doc_id,
            source_pdf=source_pdf.name if source_pdf else str(opts.get("source_pdf") or ""),
            dpi=int(opts.get("dpi") or 300),
            resume=bool(opts.get("resume", True)),
            ocr_model=str(opts.get("ocr_model") or settings.OCR_MODEL),
            text_model=str(opts.get("text_model") or settings.OLLAMA_MODEL),
        )
    job["status"] = "running"
    job["started_at"] = job.get("started_at") or job_store.utc_now()
    job_store.save_job(job)
    job_store.emit_event(job, "status", {"status": "running"})
    job_store.append_log(job, f"Starting pipeline for {normalized_doc_id}.")

    resume = bool(opts.get("resume", True))
    try:
        _run_ingest(job, source_pdf=source_pdf, dpi=int(opts.get("dpi") or 300), resume=resume)
        if _stop_if_requested(job):
            return job
        _run_folder_stage(job, "ocr", _png_pages(paths.pages_dir), resume=resume, ocr_model=str(opts.get("ocr_model") or settings.OCR_MODEL))
        if _stop_if_requested(job):
            return job
        _run_folder_stage(job, "classify", _ocr_pages(paths.ocr_dir), resume=resume, text_model=str(opts.get("text_model") or settings.OLLAMA_MODEL))
        _propagate_classify_skips(job)
        if _stop_if_requested(job):
            return job
        _run_folder_stage(job, "names", _extractable_pages(paths), resume=resume, text_model=str(opts.get("text_model") or settings.OLLAMA_MODEL))
        _propagate_name_skips(job)
        if _stop_if_requested(job):
            return job
        _run_folder_stage(job, "meta", _pages_with_names(paths), resume=resume, text_model=str(opts.get("text_model") or settings.OLLAMA_MODEL))
        if _stop_if_requested(job):
            return job
        _run_folder_stage(job, "places", _pages_with_names(paths), resume=resume, text_model=str(opts.get("text_model") or settings.OLLAMA_MODEL))
        if _stop_if_requested(job):
            return job
        _run_aggregate(job)
        final_status = "done_with_errors" if _has_failed_pages(job) else "done"
        job_store.append_log(job, f"Pipeline finished with status {final_status}.")
        job_store.finalize_job(job, final_status)
        return job
    except Exception as exc:
        job_store.append_log(job, f"Pipeline failed: {exc}")
        job_store.finalize_job(job, "failed", error=str(exc))
        return job


def _run_ingest(job: dict[str, Any], *, source_pdf: Path | None, dpi: int, resume: bool) -> None:
    doc_id = str(job["doc_id"])
    paths = doc_paths(doc_id)
    existing_manifest = _read_json_optional(paths.manifest())
    if existing_manifest and isinstance(existing_manifest.get("page_count"), int):
        job_store.ensure_pages(job, int(existing_manifest["page_count"]))
        _prime_running(job, "ingest", list(range(1, int(existing_manifest["page_count"]) + 1)))
        job_store.save_job(job)

    if source_pdf is None:
        if not paths.manifest().exists():
            raise FileNotFoundError(f"No PDF source or existing manifest found for {doc_id}")
        manifest = _read_json_optional(paths.manifest())
        page_count = int(manifest.get("page_count") or 0)
        job_store.ensure_pages(job, page_count)
        for page in range(1, page_count + 1):
            state = "done" if artifact_ok(paths.page_image(page), "image") else "failed"
            job_store.mark_stage(job, "ingest", page, state=state, detail="existing page image" if state == "done" else "missing page image")
        job_store.append_log(job, f"Ingest reused existing manifest for {doc_id}.")
        job_store.save_job(job)
        job_store.emit_event(job, "page_updated", {"stage": "ingest"})
        return

    job["current_stage"] = "ingest"
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "ingest"})
    job_store.append_log(job, f"Running ingest from {source_pdf.name}.")
    tracker = _progress_tracker(job, "ingest")
    manifest = run_stage(
        "ingest",
        doc_id,
        source_pdf=source_pdf,
        dpi=dpi,
        resume=resume,
        progress=tracker,
    )
    page_count = int(manifest.get("page_count") or 0)
    job_store.ensure_pages(job, page_count)
    for page in range(1, page_count + 1):
        status = "done" if artifact_ok(paths.page_image(page), "image") else "failed"
        job_store.mark_stage(job, "ingest", page, state=status, detail="page image ready" if status == "done" else "page image missing")
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "ingest"})


def _run_folder_stage(
    job: dict[str, Any],
    stage: str,
    page_numbers: list[int],
    *,
    resume: bool,
    ocr_model: str = "",
    text_model: str = "",
) -> None:
    doc_id = str(job["doc_id"])
    if not page_numbers:
        job_store.append_log(job, f"Skipping {stage}: no eligible pages.")
        return
    job["current_stage"] = stage
    _prime_running(job, stage, page_numbers)
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": stage, "page": page_numbers[0]})
    job_store.append_log(job, f"Running {stage} for {len(page_numbers)} page(s).")
    summary = run_stage(
        stage,
        doc_id,
        resume=resume,
        ocr_model=ocr_model or None,
        text_model=text_model or None,
        progress=_progress_tracker(job, stage, page_numbers=page_numbers),
    )
    _apply_summary(job, stage, summary)
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": stage})


def _run_aggregate(job: dict[str, Any]) -> None:
    doc_id = str(job["doc_id"])
    job["current_stage"] = "aggregate"
    job_store.mark_doc_stage(job, "aggregate", "running", detail="aggregating final CSV files")
    for page in range(1, int(job.get("total_pages") or 0) + 1):
        job_store.mark_stage(job, "aggregate", page, state="running", detail="waiting for aggregation")
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "aggregate"})
    job_store.append_log(job, "Running aggregate.")
    result = run_stage("aggregate", doc_id, resume=True)
    for page in range(1, int(job.get("total_pages") or 0) + 1):
        job_store.mark_stage(job, "aggregate", page, state="done", detail="CSV rows updated")
    job_store.mark_doc_stage(job, "aggregate", "done", detail=f"Wrote final CSVs for {doc_id}")
    job["aggregate_result"] = result
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "aggregate"})


def _progress_tracker(job: dict[str, Any], stage: str, *, page_numbers: list[int] | None = None) -> Callable[[str, int, int, Path], None]:
    order = list(page_numbers or [])
    if not order:
        order = [page["page"] for page in (job.get("pages") or [])]
    current = {"index": 0}

    def callback(action: str, page: int, total: int, path: Path) -> None:
        if int(job.get("total_pages") or 0) < int(total):
            job_store.ensure_pages(job, int(total))
        if not order:
            order.extend(range(1, int(total) + 1))
        if action in {"skip", "render", "done", "error"}:
            mapped = {
                "skip": "skipped",
                "render": "done",
                "done": "done",
                "error": "failed",
            }[action]
            detail = {
                "skip": f"{path.name} skipped",
                "render": f"{path.name} rendered",
                "done": f"{path.name} ready",
                "error": f"{path.name} failed",
            }[action]
            job_store.mark_stage(job, stage, page, state=mapped, detail=detail, error=detail if mapped == "failed" else "")
            if mapped == "failed":
                _note_page(job, page, f"{stage} failed")
            job_store.save_job(job)
            job_store.emit_event(job, "page_updated", {"stage": stage, "page": page, "state": mapped})
            job_store.append_log(job, f"{stage} page {page:03d}: {mapped}.")
        current["index"] = max(current["index"], order.index(page) + 1 if page in order else current["index"])
        if current["index"] < len(order):
            next_page = order[current["index"]]
            next_state = job_store.get_page(job, next_page)[stage].get("state")
            if next_state == "pending":
                job_store.mark_stage(job, stage, next_page, state="running", detail=f"processing {stage}")
                job_store.save_job(job)
                job_store.emit_event(job, "page_updated", {"stage": stage, "page": next_page})

    return callback


def _apply_summary(job: dict[str, Any], stage: str, summary: dict[str, Any]) -> None:
    for item in summary.get("pages") or []:
        if not isinstance(item, dict):
            continue
        page = int(item.get("page") or 0)
        if page <= 0:
            continue
        raw_status = str(item.get("status") or "")
        mapped = {
            "done": "done",
            "skipped": "skipped",
            "error": "failed",
        }.get(raw_status, raw_status or "pending")
        detail = str(item.get("error") or item.get("skip_reason") or item.get("filename") or "")
        job_store.mark_stage(job, stage, page, state=mapped, detail=detail, error=str(item.get("error") or ""))
    job_store.save_job(job)


def _propagate_classify_skips(job: dict[str, Any]) -> None:
    paths = doc_paths(str(job["doc_id"]))
    for page in range(1, int(job.get("total_pages") or 0) + 1):
        classify = _read_json_optional(paths.classify(page))
        if not classify:
            if job_store.get_page(job, page)["classify"]["state"] == "failed":
                for stage in ("names", "meta", "places"):
                    job_store.mark_stage(job, stage, page, state="skipped", detail="blocked by classify error")
            continue
        if classify.get("should_extract") is False:
            reason = str(classify.get("skip_reason") or "not extractable")
            _note_page(job, page, f"skip:{reason}")
            for stage in ("names", "meta", "places"):
                job_store.mark_stage(job, stage, page, state="skipped", detail=reason)
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "classify"})


def _propagate_name_skips(job: dict[str, Any]) -> None:
    paths = doc_paths(str(job["doc_id"]))
    for page in range(1, int(job.get("total_pages") or 0) + 1):
        classify = _read_json_optional(paths.classify(page))
        names = _load_names(paths.names(page))
        if classify and classify.get("should_extract") and not names:
            _note_page(job, page, "no named people")
            for stage in ("meta", "places"):
                if job_store.get_page(job, page)[stage]["state"] == "pending":
                    job_store.mark_stage(job, stage, page, state="skipped", detail="no named people")
        elif job_store.get_page(job, page)["names"]["state"] == "failed":
            for stage in ("meta", "places"):
                job_store.mark_stage(job, stage, page, state="skipped", detail="blocked by names error")
    job_store.save_job(job)
    job_store.emit_event(job, "page_updated", {"stage": "names"})


def _prime_running(job: dict[str, Any], stage: str, page_numbers: list[int]) -> None:
    if not page_numbers:
        return
    max_page = max(page_numbers)
    if int(job.get("total_pages") or 0) < max_page:
        job_store.ensure_pages(job, max_page)
    for page in page_numbers:
        current = job_store.get_page(job, page)[stage]["state"]
        if current not in {"done", "skipped", "failed"}:
            job_store.mark_stage(job, stage, page, state="pending", detail="")
    first_page = page_numbers[0]
    if job_store.get_page(job, first_page)[stage]["state"] == "pending":
        job_store.mark_stage(job, stage, first_page, state="running", detail=f"processing {stage}")


def _stop_if_requested(job: dict[str, Any]) -> bool:
    current = job_store.load_job(str(job["doc_id"]))
    if current.get("pause_requested"):
        job.update(current)
        job_store.append_log(job, "Pause requested; stopping after current stage.")
        job_store.finalize_job(job, "paused")
        return True
    if current.get("cancel_requested"):
        job.update(current)
        job_store.append_log(job, "Cancellation requested; stopping after current stage.")
        job_store.finalize_job(job, "cancelled")
        return True
    return False


def _resolve_pdf_source(doc_id: str, raw_source: str | None) -> Path | None:
    if raw_source:
        source = Path(raw_source)
        if source.exists():
            return source
        candidate = settings.input_pdfs_dir / Path(raw_source).name
        if candidate.exists():
            return candidate
    candidate = doc_paths(doc_id).pdf
    return candidate if candidate.exists() else None


def _png_pages(pages_dir: Path) -> list[int]:
    return sorted(_page_from_path(path) for path in pages_dir.glob("p*.png") if path.is_file())


def _ocr_pages(ocr_dir: Path) -> list[int]:
    return sorted(_page_from_path(path) for path in ocr_dir.glob("p*.txt") if path.is_file())


def _extractable_pages(paths) -> list[int]:
    pages: list[int] = []
    for text_path in paths.ocr_dir.glob("p*.txt"):
        page = _page_from_path(text_path)
        record = _read_json_optional(paths.classify(page))
        if record.get("should_extract"):
            pages.append(page)
    return sorted(set(pages))


def _pages_with_names(paths) -> list[int]:
    pages: list[int] = []
    for text_path in paths.ocr_dir.glob("p*.txt"):
        page = _page_from_path(text_path)
        classify = _read_json_optional(paths.classify(page))
        if not classify.get("should_extract"):
            continue
        if _load_names(paths.names(page)):
            pages.append(page)
    return sorted(set(pages))


def _load_names(path: Path) -> list[str]:
    record = _read_json_optional(path)
    raw = record.get("named_people") if isinstance(record, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _has_failed_pages(job: dict[str, Any]) -> bool:
    for page in job.get("pages") or []:
        for stage in ("ingest", "ocr", "classify", "names", "meta", "places", "aggregate"):
            if str((page.get(stage) or {}).get("state") or "") == "failed":
                return True
    return False


def _note_page(job: dict[str, Any], page: int, note: str) -> None:
    job_store.get_page(job, page)["note"] = str(note)


def _read_json_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _page_from_path(path: Path) -> int:
    stem = path.stem
    digits = "".join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0
