from __future__ import annotations

import base64
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import cv2
from flask import Blueprint, abort, jsonify, render_template, request, send_file

from shared.config import settings
from shared.paths import doc_paths, normalize_doc_id

from .core import ocr_page, run_folder
from .preprocessing import b64_png, preprocess_page


bp = Blueprint("ocr", __name__, url_prefix="/ocr", template_folder="templates", static_folder="static")
_JOBS: dict[str, dict[str, Any]] = {}


@bp.get("/")
def index():
    docs = _list_docs()
    selected_doc_id = request.args.get("doc_id") or (docs[0]["doc_id"] if docs else "")
    pages = _list_pages(selected_doc_id) if selected_doc_id else []
    selected_page = int(request.args.get("page") or (pages[0]["page"] if pages else 0))
    return render_template("ui.html", docs=docs, pages=pages, selected_doc_id=selected_doc_id, selected_page=selected_page)


@bp.get("/docs")
def docs():
    return jsonify({"docs": _list_docs()})


@bp.get("/pages/<doc_id>")
def pages(doc_id: str):
    return jsonify({"doc_id": normalize_doc_id(doc_id), "pages": _list_pages(doc_id)})


@bp.post("/preview/<doc_id>/<int:page>")
def preview(doc_id: str, page: int):
    image = doc_paths(doc_id).page_image(page)
    if not image.exists():
        abort(404)
    img = cv2.imread(str(image))
    if img is None:
        abort(404)
    prep = preprocess_page(
        img,
        preprocess_long=int(request.json.get("preprocess_long", 2600)) if request.is_json else 2600,
        min_long_for_ocr=int(request.json.get("min_long_for_ocr", 1800)) if request.is_json else 1800,
        tile=True,
    )
    images = [
        {"label": "original", "image": _data_url(b64_png(prep.original_bgr)), "shape": list(prep.original_bgr.shape[:2])},
        {"label": "enhanced", "image": _data_url(b64_png(prep.enhanced_gray)), "shape": list(prep.enhanced_gray.shape[:2])},
        {"label": "deskewed", "image": _data_url(b64_png(prep.deskewed_gray)), "shape": list(prep.deskewed_gray.shape[:2])},
        {"label": "cropped", "image": _data_url(b64_png(prep.cropped_gray)), "shape": list(prep.cropped_gray.shape[:2])},
    ]
    for index, tile in enumerate(prep.tiles_bgr):
        images.append({"label": f"tile {index}", "image": _data_url(b64_png(tile)), "shape": list(tile.shape[:2])})
    return jsonify({"doc_id": normalize_doc_id(doc_id), "page": page, "crop_box": prep.crop_box, "images": images})


@bp.post("/run-single/<doc_id>/<int:page>")
def run_single(doc_id: str, page: int):
    paths = doc_paths(doc_id)
    image = paths.page_image(page)
    if not image.exists():
        abort(404)
    result = ocr_page(
        image,
        paths.ocr_text(page),
        model=(request.json or {}).get("model") if request.is_json else None,
        debug_dir=paths.ocr_dir / "_debug",
    )
    _upsert_single_manifest(paths.ocr_dir, result)
    return jsonify({"doc_id": paths.doc_id, "page": page, "status": result.status, "char_count": len(result.text), "text": result.text})


@bp.post("/run-all/<doc_id>")
def run_all(doc_id: str):
    paths = doc_paths(doc_id)
    if not paths.pages_dir.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    selected_model = payload.get("model")
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"job_id": job_id, "doc_id": paths.doc_id, "status": "running"}

    def worker() -> None:
        try:
            manifest = run_folder(paths.pages_dir, paths.ocr_dir, model=selected_model)
            _JOBS[job_id].update({"status": "done", "manifest": manifest})
        except Exception as exc:
            _JOBS[job_id].update({"status": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify(_JOBS[job_id])


@bp.get("/debug/<doc_id>/<int:page>")
def debug(doc_id: str, page: int):
    debug_dir = doc_paths(doc_id).ocr_dir / "_debug"
    prefix = f"p{page:03d}__"
    files = []
    if debug_dir.exists():
        for path in sorted(debug_dir.glob(prefix + "*")):
            files.append({"name": path.name, "size_bytes": path.stat().st_size})
    return jsonify({"doc_id": normalize_doc_id(doc_id), "page": page, "files": files})


@bp.get("/text/<doc_id>/<int:page>")
def text(doc_id: str, page: int):
    path = doc_paths(doc_id).ocr_text(page)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="text/plain", conditional=True)


@bp.get("/status/<doc_id>")
def status(doc_id: str):
    path = doc_paths(doc_id).ocr_dir / "manifest.json"
    if path.exists():
        return send_file(path, mimetype="application/json", conditional=True)
    return jsonify({"doc_id": normalize_doc_id(doc_id), "status": "not_started", "pages": []})


@bp.get("/jobs/<job_id>")
def job(job_id: str):
    return jsonify(_JOBS.get(job_id, {"job_id": job_id, "status": "unknown"}))


def _list_docs() -> list[dict[str, Any]]:
    root = settings.pages_root
    if not root.exists():
        return []
    docs = []
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
        count = len(list(path.glob("p*.png")))
        if count:
            docs.append({"doc_id": path.name, "pages": count})
    return docs


def _list_pages(doc_id: str) -> list[dict[str, Any]]:
    if not doc_id:
        return []
    paths = doc_paths(doc_id)
    pages = []
    for image in sorted(paths.pages_dir.glob("p*.png")):
        page = int("".join(ch for ch in image.stem if ch.isdigit()) or 0)
        text_path = paths.ocr_text(page)
        pages.append({"page": page, "filename": image.name, "has_text": text_path.exists(), "char_count": text_path.stat().st_size if text_path.exists() else 0})
    return pages


def _data_url(encoded: str) -> str:
    return f"data:image/png;base64,{encoded}"


def _upsert_single_manifest(out_dir: Path, result) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            manifest = {}
    else:
        manifest = {}
    manifest.setdefault("doc_id", out_dir.name)
    manifest.setdefault("pages", [])
    page = int("".join(ch for ch in result.image_path.stem if ch.isdigit()) or 0)
    entry = {
        "page": page,
        "filename": result.image_path.name,
        "text_file": result.out_file.name,
        "status": result.status,
        "char_count": len(result.text),
        "model": result.model,
        "tile_count": result.tile_count,
        "elapsed_seconds": result.elapsed_seconds,
    }
    manifest["pages"] = [item for item in manifest.get("pages", []) if item.get("page") != page] + [entry]
    manifest["pages"].sort(key=lambda item: int(item.get("page", 0)))
    manifest["completed_pages"] = len([item for item in manifest["pages"] if item.get("status") in {"done", "skipped"}])
    manifest["status"] = "partial"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
