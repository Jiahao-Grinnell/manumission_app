from __future__ import annotations

import json
import re
import threading
import uuid
from html import escape
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id

from .core import ClassificationResult, classify_file, run_folder
from .parsing import choose_report_type
from .rules import normalize_for_match


bp = Blueprint(
    "page_classifier",
    __name__,
    url_prefix="/classify",
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
    if not text_path.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    result = classify_file(
        text_path,
        paths.classify(page),
        model=payload.get("model"),
        report_type_override=payload.get("report_type") or None,
    )
    return jsonify(_page_payload(paths.doc_id, page, result=result))


@bp.post("/run-all/<doc_id>")
def run_all(doc_id: str):
    paths = doc_paths(doc_id)
    if not paths.ocr_dir.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    selected_model = payload.get("model")
    report_type_override = payload.get("report_type") or None
    resume = bool(payload.get("resume", True))
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"job_id": job_id, "doc_id": paths.doc_id, "status": "running"}

    def worker() -> None:
        try:
            manifest = run_folder(
                paths.ocr_dir,
                paths.inter_dir,
                model=selected_model,
                report_type_override=report_type_override,
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
        text_count = len(list(path.glob("p*.txt")))
        if not text_count:
            continue
        inter_dir = settings.intermediate_root / path.name
        classify_count = len(list(inter_dir.glob("p*.classify.json"))) if inter_dir.exists() else 0
        docs.append({"doc_id": path.name, "pages": text_count, "classified_pages": classify_count})
    return docs


def _list_pages(doc_id: str) -> list[dict[str, Any]]:
    if not doc_id:
        return []
    paths = doc_paths(doc_id)
    pages = []
    for text_path in sorted(paths.ocr_dir.glob("p*.txt")):
        page = _page_number(text_path)
        result_record = _read_json_dict(paths.classify(page))
        pages.append(
            {
                "page": page,
                "filename": text_path.name,
                "char_count": len(text_path.read_text(encoding="utf-8", errors="replace")),
                "has_result": bool(result_record),
                "report_type": choose_report_type(str(result_record.get("report_type") or "correspondence")) if result_record else None,
                "should_extract": result_record.get("should_extract"),
                "skip_reason": result_record.get("skip_reason"),
            }
        )
    return pages


def _page_payload(doc_id: str, page: int, *, result: ClassificationResult | None = None) -> dict[str, Any] | None:
    normalized_doc_id = normalize_doc_id(doc_id)
    paths = doc_paths(normalized_doc_id)
    text_path = paths.ocr_text(page)
    if not text_path.exists():
        return None
    ocr_text = text_path.read_text(encoding="utf-8", errors="replace")
    result_record = result.as_dict() if result is not None else _normalized_result_record(_read_json_dict(paths.classify(page)))
    highlighted = _highlight_text(ocr_text, str(result_record.get("evidence") or "")) if result_record else _highlight_text(ocr_text, "")
    return {
        "doc_id": normalized_doc_id,
        "page": page,
        "ocr_text": ocr_text,
        "ocr_char_count": len(ocr_text),
        "result": result_record or None,
        "source_html": highlighted["html"],
        "evidence_located": highlighted["located"],
    }


def _highlight_text(text: str, evidence: str) -> dict[str, Any]:
    span = _find_evidence_span(text, evidence)
    if not span:
        return {"html": escape(text or ""), "located": False}
    start, end = span
    return {
        "html": f"{escape(text[:start])}<mark>{escape(text[start:end])}</mark>{escape(text[end:])}",
        "located": True,
    }


def _find_evidence_span(text: str, evidence: str) -> tuple[int, int] | None:
    if not text or not evidence:
        return None
    direct_index = text.casefold().find(evidence.casefold())
    if direct_index >= 0:
        return direct_index, direct_index + len(evidence)

    evidence_tokens = [token for token, _, _ in _normalized_tokens(evidence)]
    text_tokens = _normalized_tokens(text)
    if not evidence_tokens or len(text_tokens) < len(evidence_tokens):
        return None

    for start in range(0, len(text_tokens) - len(evidence_tokens) + 1):
        window = text_tokens[start : start + len(evidence_tokens)]
        if [item[0] for item in window] == evidence_tokens:
            return window[0][1], window[-1][2]
    return None


def _normalized_tokens(text: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S+", text or ""):
        normalized = normalize_for_match(match.group(0)).replace(" ", "")
        if normalized:
            tokens.append((normalized, match.start(), match.end()))
    return tokens


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalized_result_record(record: dict[str, Any]) -> dict[str, Any]:
    if not record:
        return {}
    normalized = dict(record)
    normalized["report_type"] = choose_report_type(str(record.get("report_type") or "correspondence"))
    initial_decision = normalized.get("initial_decision")
    if isinstance(initial_decision, dict):
        initial_copy = dict(initial_decision)
        initial_copy["report_type"] = choose_report_type(str(initial_decision.get("report_type") or "correspondence"))
        normalized["initial_decision"] = initial_copy
    override = normalized.get("override")
    if isinstance(override, dict):
        override_copy = dict(override)
        override_copy["from"] = choose_report_type(str(override.get("from") or "correspondence"))
        override_copy["to"] = choose_report_type(str(override.get("to") or "correspondence"))
        normalized["override"] = override_copy
    return normalized


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
