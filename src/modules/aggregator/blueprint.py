from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request, send_file

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id

from .core import aggregate
from .stats import build_stats, read_csv_preview


CSV_FILES = {
    "detail": "Detailed info.csv",
    "places": "name place.csv",
    "status": "run_status.csv",
}


bp = Blueprint(
    "aggregator",
    __name__,
    url_prefix="/aggregate",
    template_folder="templates",
    static_folder="static",
)


@bp.get("/")
def index():
    selected_doc_id = request.args.get("doc_id")
    docs = _list_docs()
    if not selected_doc_id and docs:
        selected_doc_id = docs[0]["doc_id"]
    result = _result_payload(selected_doc_id) if selected_doc_id else None
    return render_template("ui.html", docs=docs, selected_doc_id=selected_doc_id, result=result)


@bp.get("/docs")
def docs():
    return jsonify({"docs": _list_docs()})


@bp.post("/run/<doc_id>")
def run(doc_id: str):
    result = aggregate(doc_id)
    return jsonify(_result_payload(result.doc_id, result=result))


@bp.get("/result/<doc_id>")
def result(doc_id: str):
    return jsonify(_result_payload(doc_id))


@bp.get("/stats/<doc_id>")
def stats(doc_id: str):
    return jsonify(_result_payload(doc_id).get("stats", {}))


@bp.get("/download/<doc_id>.zip")
def download_zip(doc_id: str):
    output_dir = doc_paths(doc_id).output_dir
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in CSV_FILES.values():
            path = output_dir / filename
            if path.exists():
                archive.write(path, filename)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=f"{normalize_doc_id(doc_id)}.zip")


@bp.get("/download/<doc_id>/<path:name>")
def download_csv(doc_id: str, name: str):
    filename = Path(name).name
    if filename not in CSV_FILES.values():
        abort(404)
    path = doc_paths(doc_id).output_dir / filename
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="text/csv", as_attachment=True, download_name=filename)


def _list_docs() -> list[dict[str, Any]]:
    root = settings.intermediate_root
    if not root.exists():
        return []
    docs = []
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
        json_count = len(list(path.glob("p*.json")))
        if json_count:
            docs.append({"doc_id": path.name, "json_files": json_count})
    return docs


def _result_payload(doc_id: str, *, result=None) -> dict[str, Any]:
    normalized_doc_id = normalize_doc_id(doc_id)
    paths = doc_paths(normalized_doc_id)
    if result is None:
        summary = _read_summary(paths.output_dir / "aggregation_summary.json")
        if summary:
            stats_payload = summary.get("stats", {})
            cleanup_actions = summary.get("cleanup_actions", [])
        else:
            detail_rows = read_csv_preview(paths.output_dir / CSV_FILES["detail"], limit=10000)
            place_rows = read_csv_preview(paths.output_dir / CSV_FILES["places"], limit=10000)
            status_rows = read_csv_preview(paths.output_dir / CSV_FILES["status"], limit=10000)
            stats_payload = build_stats(detail_rows, place_rows, status_rows) if (detail_rows or place_rows or status_rows) else {}
            cleanup_actions = []
    else:
        stats_payload = result.stats
        cleanup_actions = result.cleanup_actions
    return {
        "doc_id": normalized_doc_id,
        "stats": stats_payload,
        "cleanup_actions": cleanup_actions,
        "files": {
            key: {
                "filename": filename,
                "exists": (paths.output_dir / filename).exists(),
                "rows": read_csv_preview(paths.output_dir / filename),
            }
            for key, filename in CSV_FILES.items()
        },
    }


def _read_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
