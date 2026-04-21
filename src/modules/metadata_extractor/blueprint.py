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
from shared.text_utils import normalize_ws, strip_accents

from .core import MetadataPageResult, run_folder, run_page_file
from .parsing import DETAIL_FIELD_ORDER, FIELD_LABELS


bp = Blueprint(
    "metadata_extractor",
    __name__,
    url_prefix="/meta",
    template_folder="templates",
    static_folder="static",
)

_JOBS: dict[str, dict[str, Any]] = {}
FIELD_CLASSES = {
    "report_type": "highlight-report",
    "crime_type": "highlight-crime",
    "whether_abuse": "highlight-abuse",
    "conflict_type": "highlight-conflict",
    "trial": "highlight-trial",
    "amount_paid": "highlight-amount",
}


@bp.get("/")
def index():
    docs = _list_docs()
    selected_doc_id = request.args.get("doc_id") or (docs[0]["doc_id"] if docs else "")
    pages = _list_pages(selected_doc_id) if selected_doc_id else []
    selected_page = _selected_page(request.args.get("page"), pages)
    people = _list_people(selected_doc_id, selected_page) if selected_doc_id and selected_page else []
    selected_name = _selected_name(request.args.get("name"), people)
    page_data = _page_payload(selected_doc_id, selected_page, selected_name) if selected_doc_id and selected_page else None
    return render_template(
        "ui.html",
        docs=docs,
        pages=pages,
        people=people,
        selected_doc_id=selected_doc_id,
        selected_page=selected_page,
        selected_name=selected_name,
        initial_page_data=page_data,
    )


@bp.get("/docs")
def docs():
    return jsonify({"docs": _list_docs()})


@bp.get("/pages/<doc_id>")
def pages(doc_id: str):
    normalized_doc_id = normalize_doc_id(doc_id)
    return jsonify({"doc_id": normalized_doc_id, "pages": _list_pages(normalized_doc_id)})


@bp.get("/people/<doc_id>/<int:page>")
def people(doc_id: str, page: int):
    normalized_doc_id = normalize_doc_id(doc_id)
    return jsonify({"doc_id": normalized_doc_id, "page": page, "people": _list_people(normalized_doc_id, page)})


@bp.post("/run-single/<doc_id>/<int:page>/<path:name>")
def run_single(doc_id: str, page: int, name: str):
    paths = doc_paths(doc_id)
    payload = request.get_json(silent=True) or {}
    result = run_page_file(
        paths.ocr_text(page),
        paths.classify(page),
        paths.names(page),
        paths.meta(page),
        model=payload.get("model"),
        person_name=name,
    )
    return jsonify(_page_payload(paths.doc_id, page, name, result=result))


@bp.post("/run-page/<doc_id>/<int:page>")
def run_page(doc_id: str, page: int):
    paths = doc_paths(doc_id)
    payload = request.get_json(silent=True) or {}
    selected_name = str(payload.get("name") or "")
    result = run_page_file(
        paths.ocr_text(page),
        paths.classify(page),
        paths.names(page),
        paths.meta(page),
        model=payload.get("model"),
    )
    return jsonify(_page_payload(paths.doc_id, page, selected_name, result=result))


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
    payload = _page_payload(doc_id, page, request.args.get("name") or "")
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
        completed = sum(1 for page in pages if page["has_result"])
        docs.append({"doc_id": path.name, "pages": len(pages), "completed_pages": completed})
    return docs


def _list_pages(doc_id: str) -> list[dict[str, Any]]:
    if not doc_id:
        return []
    paths = doc_paths(doc_id)
    pages = []
    for text_path in sorted(paths.ocr_dir.glob("p*.txt")):
        page = _page_number(text_path)
        classify_record = _read_json_dict(paths.classify(page))
        names_record = _read_json_dict(paths.names(page))
        people = _names_from_record(names_record)
        if not classify_record.get("should_extract") or not people:
            continue
        result_record = _read_json_dict(paths.meta(page))
        pages.append(
            {
                "page": page,
                "filename": text_path.name,
                "char_count": len(text_path.read_text(encoding="utf-8", errors="replace")),
                "has_result": bool(result_record.get("people")),
                "report_type": classify_record.get("report_type") or "correspondence",
                "people": len(people),
                "rows": len(result_record.get("rows") or []),
            }
        )
    return pages


def _list_people(doc_id: str, page: int) -> list[str]:
    if not doc_id or not page:
        return []
    paths = doc_paths(doc_id)
    return _names_from_record(_read_json_dict(paths.names(page)))


def _page_payload(
    doc_id: str,
    page: int,
    selected_name: str,
    *,
    result: MetadataPageResult | None = None,
) -> dict[str, Any] | None:
    normalized_doc_id = normalize_doc_id(doc_id)
    paths = doc_paths(normalized_doc_id)
    text_path = paths.ocr_text(page)
    classify_path = paths.classify(page)
    names_path = paths.names(page)
    if not text_path.exists() or not classify_path.exists() or not names_path.exists():
        return None
    classify_record = _read_json_dict(classify_path)
    if not classify_record.get("should_extract"):
        return None

    names = _names_from_record(_read_json_dict(names_path))
    if not names:
        return None

    result_record = result.as_dict() if result is not None else _read_json_dict(paths.meta(page))
    chosen_name = _selected_name(selected_name, names)
    selected_person = _person_record(result_record, chosen_name)
    ocr_text = text_path.read_text(encoding="utf-8", errors="replace")
    highlighted = _highlight_evidence(ocr_text, selected_person)
    return {
        "doc_id": normalized_doc_id,
        "page": page,
        "ocr_text": ocr_text,
        "ocr_char_count": len(ocr_text),
        "classify": classify_record,
        "names": names,
        "selected_name": chosen_name,
        "selected_person": selected_person,
        "result": result_record or None,
        "source_html": highlighted["html"],
        "highlighted_fields": highlighted["field_counts"],
    }


def _highlight_evidence(text: str, selected_person: dict[str, Any] | None) -> dict[str, Any]:
    if not text or not selected_person:
        return {"html": escape(text or ""), "field_counts": {}}
    row = selected_person.get("row") if isinstance(selected_person, dict) else {}
    evidence_map = row.get("_evidence") if isinstance(row, dict) else {}
    spans: list[dict[str, Any]] = []
    field_counts: dict[str, int] = {}
    for field in DETAIL_FIELD_ORDER:
        evidence = str((evidence_map or {}).get(field) or "").strip()
        if not evidence:
            continue
        span = _find_evidence_span(text, evidence)
        if not span:
            continue
        field_counts[field] = field_counts.get(field, 0) + 1
        spans.append(
            {
                "start": span[0],
                "end": span[1],
                "field": field,
                "css": FIELD_CLASSES[field],
            }
        )

    chosen = _non_overlapping_spans(spans)
    cursor = 0
    parts: list[str] = []
    for span in chosen:
        parts.append(escape(text[cursor : span["start"]]))
        parts.append(
            f'<mark class="{span["css"]}" title="{escape(FIELD_LABELS[span["field"]])}">{escape(text[span["start"] : span["end"]])}</mark>'
        )
        cursor = span["end"]
    parts.append(escape(text[cursor:]))
    return {"html": "".join(parts), "field_counts": field_counts}


def _find_evidence_span(text: str, evidence: str) -> tuple[int, int] | None:
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
        normalized = _normalize_for_match(match.group(0)).replace(" ", "")
        if normalized:
            tokens.append((normalized, match.start(), match.end()))
    return tokens


def _normalize_for_match(text: str) -> str:
    normalized = strip_accents(normalize_ws(text)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _non_overlapping_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda item: (item["start"], item["end"])):
        if any(not (span["end"] <= other["start"] or span["start"] >= other["end"]) for other in chosen):
            continue
        chosen.append(span)
    return chosen


def _names_from_record(record: dict[str, Any]) -> list[str]:
    raw = record.get("named_people") if isinstance(record, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
    return names


def _person_record(record: dict[str, Any], selected_name: str) -> dict[str, Any] | None:
    people = record.get("people") if isinstance(record, dict) else None
    if not isinstance(people, list):
        return None
    for person in people:
        if isinstance(person, dict) and str(person.get("name") or "").casefold() == selected_name.casefold():
            return person
    return None


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


def _selected_name(raw: str | None, people: list[str]) -> str:
    if raw:
        for name in people:
            if name.casefold() == raw.casefold():
                return name
    return people[0] if people else ""


def _page_number(path: Path) -> int:
    match = re.search(r"p(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0

