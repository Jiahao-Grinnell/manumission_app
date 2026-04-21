from __future__ import annotations

import json
import re
import threading
import uuid
from html import escape
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request

from modules.normalizer.names import build_name_regex
from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id

from .core import RERUN_ALLOWED, NameExtractionResult, extract_file, rerun_pass_file, run_folder


bp = Blueprint(
    "name_extractor",
    __name__,
    url_prefix="/names",
    template_folder="templates",
    static_folder="static",
)

_JOBS: dict[str, dict[str, Any]] = {}


@bp.get("/")
def index():
    docs = _list_docs()
    selected_doc_id = request.args.get("doc_id") or (docs[0]["doc_id"] if docs else "")
    pages = _list_pages(selected_doc_id) if selected_doc_id else []
    selected_page = _selected_page(request.args.get("page"), pages)
    page_data = _page_payload(selected_doc_id, selected_page) if selected_doc_id and selected_page else None
    return render_template(
        "ui.html",
        docs=docs,
        pages=pages,
        selected_doc_id=selected_doc_id,
        selected_page=selected_page,
        rerun_allowed=RERUN_ALLOWED,
        initial_page_data=page_data,
    )


@bp.get("/docs")
def docs():
    return jsonify({"docs": _list_docs()})


@bp.get("/pages/<doc_id>")
def pages(doc_id: str):
    normalized_doc_id = normalize_doc_id(doc_id)
    return jsonify({"doc_id": normalized_doc_id, "pages": _list_pages(normalized_doc_id)})


@bp.post("/run-single/<doc_id>/<int:page>")
def run_single(doc_id: str, page: int):
    paths = doc_paths(doc_id)
    text_path = paths.ocr_text(page)
    classify_path = paths.classify(page)
    if not text_path.exists() or not classify_path.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    result = extract_file(
        text_path,
        classify_path,
        paths.names(page),
        model=payload.get("model"),
    )
    return jsonify(_page_payload(paths.doc_id, page, result=result))


@bp.post("/rerun-pass/<doc_id>/<int:page>/<pass_name>")
def rerun_pass(doc_id: str, page: int, pass_name: str):
    if pass_name not in RERUN_ALLOWED:
        abort(404)
    paths = doc_paths(doc_id)
    text_path = paths.ocr_text(page)
    classify_path = paths.classify(page)
    if not text_path.exists() or not classify_path.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    result = rerun_pass_file(
        text_path,
        classify_path,
        paths.names(page),
        pass_name,
        model=payload.get("model"),
    )
    return jsonify(_page_payload(paths.doc_id, page, result=result))


@bp.post("/run-all/<doc_id>")
def run_all(doc_id: str):
    paths = doc_paths(doc_id)
    if not paths.ocr_dir.exists() or not paths.inter_dir.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    selected_model = payload.get("model")
    resume = bool(payload.get("resume", True))
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"job_id": job_id, "doc_id": paths.doc_id, "status": "running"}

    def worker() -> None:
        try:
            manifest = run_folder(
                paths.ocr_dir,
                paths.inter_dir,
                paths.inter_dir,
                model=selected_model,
                resume=resume,
            )
            _JOBS[job_id].update({"status": "done", "manifest": manifest})
        except Exception as exc:
            _JOBS[job_id].update({"status": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify(_JOBS[job_id])


@bp.get("/result/<doc_id>/<int:page>")
def result(doc_id: str, page: int):
    payload = _page_payload(doc_id, page)
    if payload is None:
        abort(404)
    return jsonify(payload)


@bp.get("/jobs/<job_id>")
def job(job_id: str):
    return jsonify(_JOBS.get(job_id, {"job_id": job_id, "status": "unknown"}))


def _list_docs() -> list[dict[str, Any]]:
    root = settings.ocr_root
    if not root.exists():
        return []
    docs = []
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
        pages = _list_pages(path.name)
        if not pages:
            continue
        complete = sum(1 for page in pages if page["has_result"])
        docs.append({"doc_id": path.name, "pages": len(pages), "completed_pages": complete})
    return docs


def _list_pages(doc_id: str) -> list[dict[str, Any]]:
    if not doc_id:
        return []
    paths = doc_paths(doc_id)
    pages = []
    for text_path in sorted(paths.ocr_dir.glob("p*.txt")):
        page = _page_number(text_path)
        classify_record = _read_json_dict(paths.classify(page))
        if not classify_record.get("should_extract"):
            continue
        result_record = _read_json_dict(paths.names(page))
        pages.append(
            {
                "page": page,
                "filename": text_path.name,
                "char_count": len(text_path.read_text(encoding="utf-8", errors="replace")),
                "has_result": bool(result_record),
                "report_type": classify_record.get("report_type") or "correspondence",
                "named_people": len(result_record.get("named_people") or []),
            }
        )
    return pages


def _page_payload(doc_id: str, page: int, *, result: NameExtractionResult | None = None) -> dict[str, Any] | None:
    normalized_doc_id = normalize_doc_id(doc_id)
    paths = doc_paths(normalized_doc_id)
    text_path = paths.ocr_text(page)
    classify_path = paths.classify(page)
    if not text_path.exists() or not classify_path.exists():
        return None
    classify_record = _read_json_dict(classify_path)
    if not classify_record.get("should_extract"):
        return None

    ocr_text = text_path.read_text(encoding="utf-8", errors="replace")
    result_record = result.as_dict() if result is not None else _read_json_dict(paths.names(page))
    highlighted = _highlight_text(ocr_text, result_record.get("named_people") or [], result_record.get("removed_candidates") or [])
    return {
        "doc_id": normalized_doc_id,
        "page": page,
        "ocr_text": ocr_text,
        "ocr_char_count": len(ocr_text),
        "classify": classify_record,
        "result": result_record or None,
        "source_html": highlighted["html"],
        "highlighted_subjects": highlighted["subject_count"],
        "highlighted_dropped": highlighted["drop_count"],
    }


def _highlight_text(text: str, final_people: list[dict[str, Any]], dropped_people: list[dict[str, Any]]) -> dict[str, Any]:
    if not text:
        return {"html": "", "subject_count": 0, "drop_count": 0}
    spans = _collect_spans(text, final_people, "subject", priority=2)
    spans.extend(_collect_spans(text, dropped_people, "drop", priority=1))
    selected = _non_overlapping_spans(spans)
    cursor = 0
    parts: list[str] = []
    subject_count = 0
    drop_count = 0

    for span in selected:
        start, end = span["start"], span["end"]
        parts.append(escape(text[cursor:start]))
        css = "subject-hit" if span["kind"] == "subject" else "drop-hit"
        parts.append(f'<mark class="{css}" title="{escape(span["name"])}">{escape(text[start:end])}</mark>')
        cursor = end
        if span["kind"] == "subject":
            subject_count += 1
        else:
            drop_count += 1

    parts.append(escape(text[cursor:]))
    return {"html": "".join(parts), "subject_count": subject_count, "drop_count": drop_count}


def _collect_spans(text: str, items: list[dict[str, Any]], kind: str, *, priority: int) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen_names:
            continue
        seen_names.add(key)
        pattern = build_name_regex(name)
        if not pattern:
            continue
        for match in pattern.finditer(text):
            spans.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "kind": kind,
                    "priority": priority,
                    "name": name,
                }
            )
    return spans


def _non_overlapping_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda item: (item["start"], -item["priority"], -(item["end"] - item["start"]))):
        if any(not (span["end"] <= other["start"] or span["start"] >= other["end"]) for other in chosen):
            continue
        chosen.append(span)
    return sorted(chosen, key=lambda item: item["start"])


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _selected_page(raw: str | None, pages: list[dict[str, Any]]) -> int:
    if raw:
        try:
            selected = int(raw)
        except Exception:
            selected = 0
        else:
            if any(item["page"] == selected for item in pages):
                return selected
    return pages[0]["page"] if pages else 0


def _page_number(path: Path) -> int:
    match = re.search(r"p(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0
