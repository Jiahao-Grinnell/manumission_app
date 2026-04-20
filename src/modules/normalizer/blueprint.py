from __future__ import annotations

import csv
import io
import json
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from .dates import explain_date_parse
from .evidence import clean_evidence, normalize_for_match
from .names import explain_name_comparison, is_valid_name, normalize_name
from .places import dedupe_place_rows, is_valid_place, normalize_place


bp = Blueprint(
    "normalizer",
    __name__,
    url_prefix="/normalizer",
    template_folder="templates",
    static_folder="static",
)


@bp.get("/")
def index():
    return render_template("ui.html")


@bp.post("/normalize/name")
def normalize_name_api():
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("raw") or "")
    normalized = normalize_name(raw)
    valid = is_valid_name(normalized)
    return jsonify({"raw": raw, "normalized": normalized, "valid": valid, "reason": "" if valid else "not a valid subject name"})


@bp.post("/normalize/place")
def normalize_place_api():
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("raw") or "")
    normalized = normalize_place(raw)
    valid = is_valid_place(normalized)
    return jsonify({"raw": raw, "normalized": normalized, "valid": valid, "reason": "" if valid else "not a valid place"})


@bp.post("/normalize/date")
def normalize_date_api():
    payload = request.get_json(silent=True) or {}
    doc_year = _optional_int(payload.get("doc_year"))
    return jsonify(explain_date_parse(str(payload.get("raw") or ""), doc_year))


@bp.post("/normalize/evidence")
def normalize_evidence_api():
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("raw") or "")
    return jsonify({"cleaned": clean_evidence(raw), "match_text": normalize_for_match(raw)})


@bp.post("/compare-names")
def compare_names_api():
    payload = request.get_json(silent=True) or {}
    return jsonify(explain_name_comparison(str(payload.get("a") or ""), str(payload.get("b") or "")))


@bp.post("/dedupe-places")
def dedupe_places_api():
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("raw") or "")
    rows = _parse_rows(raw)
    deduped = dedupe_place_rows(rows)
    return jsonify({"input_count": len(rows), "output_count": len(deduped), "rows": deduped})


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_rows(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("rows")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]
