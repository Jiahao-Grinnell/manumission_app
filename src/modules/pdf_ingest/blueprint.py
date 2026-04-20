from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id
from shared.storage import read_json

from .core import ingest


bp = Blueprint(
    "pdf_ingest",
    __name__,
    url_prefix="/ingest",
    template_folder="templates",
    static_folder="static",
)


def _parse_dpi(value: str | int | None) -> int:
    if value is None or str(value).strip() == "":
        return 300
    try:
        dpi = int(value)
    except (TypeError, ValueError):
        abort(400, "DPI must be a number")
    if dpi < 72 or dpi > 600:
        abort(400, "DPI must be between 72 and 600")
    return dpi


def _derive_doc_id(filename: str, explicit_doc_id: str | None = None) -> str:
    try:
        return normalize_doc_id(explicit_doc_id or Path(filename).stem)
    except ValueError as exc:
        abort(400, str(exc))


def _safe_input_pdf(filename: str) -> Path:
    name = Path(filename).name
    if name != filename:
        abort(400, "Invalid input PDF name")
    path = settings.input_pdfs_dir / name
    if path.suffix.lower() != ".pdf" or not path.exists() or not path.is_file():
        abort(404, "Input PDF not found")
    return path


def _list_input_pdfs() -> list[dict[str, Any]]:
    root = settings.input_pdfs_dir
    if not root.exists():
        return []
    files = []
    for path in sorted(root.glob("*.pdf"), key=lambda item: item.name.lower()):
        files.append({"name": path.name, "size_bytes": path.stat().st_size})
    return files


def _list_docs() -> list[dict[str, Any]]:
    root = settings.pages_root
    if not root.exists():
        return []
    docs = []
    for manifest_path in sorted(root.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        docs.append(
            {
                "doc_id": manifest.get("doc_id", manifest_path.parent.name),
                "status": manifest.get("status", "unknown"),
                "completed_pages": manifest.get("completed_pages", 0),
                "page_count": manifest.get("page_count", 0),
                "updated_at": manifest.get("updated_at", ""),
            }
        )
    return docs


def _load_manifest(doc_id: str | None) -> dict[str, Any] | None:
    if not doc_id:
        return None
    manifest_path = doc_paths(doc_id).manifest()
    if not manifest_path.exists():
        return None
    data = read_json(manifest_path)
    return data if isinstance(data, dict) else None


@bp.get("/")
def index():
    selected_doc_id = request.args.get("doc_id")
    docs = _list_docs()
    if not selected_doc_id and docs:
        selected_doc_id = docs[0]["doc_id"]
    manifest = _load_manifest(selected_doc_id)
    return render_template(
        "ui.html",
        input_pdfs=_list_input_pdfs(),
        docs=docs,
        manifest=manifest,
        selected_doc_id=selected_doc_id,
    )


@bp.post("/upload")
def upload():
    uploaded = request.files.get("pdf")
    if uploaded is None or uploaded.filename == "":
        abort(400, "Choose a PDF to upload")
    if not uploaded.filename.lower().endswith(".pdf"):
        abort(400, "Only PDF uploads are accepted")

    doc_id = _derive_doc_id(uploaded.filename, request.form.get("doc_id"))
    dpi = _parse_dpi(request.form.get("dpi"))
    settings.input_pdfs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = settings.input_pdfs_dir / f"{doc_id}.pdf"
    uploaded.save(pdf_path)

    manifest = ingest(pdf_path, doc_paths(doc_id).pages_dir, dpi=dpi, doc_id=doc_id)
    flash(f"{doc_id}: {manifest['completed_pages']} pages ready.")
    return redirect(url_for("pdf_ingest.index", doc_id=doc_id))


@bp.post("/register")
def register():
    source_name = request.form.get("source_pdf", "")
    source_path = _safe_input_pdf(source_name)
    doc_id = _derive_doc_id(source_path.name, request.form.get("doc_id"))
    dpi = _parse_dpi(request.form.get("dpi"))
    manifest = ingest(source_path, doc_paths(doc_id).pages_dir, dpi=dpi, doc_id=doc_id)
    flash(f"{doc_id}: {manifest['completed_pages']} pages ready.")
    return redirect(url_for("pdf_ingest.index", doc_id=doc_id))


@bp.post("/run")
def run():
    payload = request.get_json(silent=True) or request.form
    raw_doc_id = str(payload.get("doc_id", "")).strip()
    if not raw_doc_id:
        abort(400, "doc_id is required")
    doc_id = _derive_doc_id(raw_doc_id, raw_doc_id)
    source_name = payload.get("source_pdf")
    pdf_path = _safe_input_pdf(str(source_name)) if source_name else settings.input_pdfs_dir / f"{doc_id}.pdf"
    if not pdf_path.exists():
        abort(404, "Input PDF not found")
    dpi = _parse_dpi(payload.get("dpi"))
    start_page = int(payload.get("start_page", 1))
    end_page_raw = payload.get("end_page")
    end_page = int(end_page_raw) if end_page_raw not in {None, ""} else None
    manifest = ingest(pdf_path, doc_paths(doc_id).pages_dir, dpi=dpi, doc_id=doc_id, start_page=start_page, end_page=end_page)
    return jsonify(manifest)


@bp.get("/manifest/<doc_id>")
def manifest(doc_id: str):
    data = _load_manifest(doc_id)
    if data is None:
        abort(404, "Manifest not found")
    return jsonify(data)


@bp.get("/thumb/<doc_id>/<int:page>")
def thumb(doc_id: str, page: int):
    return _send_page_image(doc_id, page)


@bp.get("/page/<doc_id>/<int:page>")
def page(doc_id: str, page: int):
    return _send_page_image(doc_id, page)


def _send_page_image(doc_id: str, page: int):
    if page < 1:
        abort(404)
    image_path = doc_paths(doc_id).page_image(page)
    if not image_path.exists():
        abort(404, "Page image not found")
    return send_file(image_path, mimetype="image/png", conditional=True)
