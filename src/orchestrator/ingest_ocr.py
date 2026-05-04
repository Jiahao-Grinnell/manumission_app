from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import fitz
import numpy as np

from modules.ocr.core import ocr_image_bgr, should_skip_existing, wait_for_ollama_ready
from shared.config import settings
from shared.paths import normalize_doc_id
from shared.storage import write_json_atomic


ProgressCallback = Callable[[str, int, int, Path], None]


def run_ingest_ocr(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = 300,
    doc_id: str | None = None,
    model: str | None = None,
    ollama_generate_url: str | None = None,
    resume: bool = True,
    debug: bool = True,
    tile: bool = True,
    max_new_tokens: int = 1200,
    prompt: str | None = None,
    timeout_s: int = 240,
    wait_ready: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Render each PDF page in memory and OCR it immediately.

    This is the orchestrator path for production runs. It intentionally does
    not persist page images; only OCR text and an OCR manifest are written.
    The standalone ingest and OCR modules still support image-based debugging.
    """

    pdf = Path(pdf_path)
    output = Path(out_dir)
    if not pdf.exists() or not pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {pdf}")
    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")

    output.mkdir(parents=True, exist_ok=True)
    selected_model = model or settings.OCR_MODEL
    selected_url = ollama_generate_url or settings.OLLAMA_URL
    normalized_doc_id = normalize_doc_id(doc_id or output.name or pdf.stem)

    if wait_ready:
        wait_for_ollama_ready(selected_url, timeout_s=240)

    source_sha = _sha256(pdf)
    source_size = pdf.stat().st_size
    manifest_path = output / "manifest.json"
    existing = _read_manifest(manifest_path)
    document = fitz.open(str(pdf))
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise RuntimeError(f"Encrypted PDF cannot be opened without a password: {pdf}")

        page_count = document.page_count
        compatible = _compatible_manifest(
            existing,
            source_sha=source_sha,
            page_count=page_count,
            dpi=dpi,
        )
        can_resume = bool(resume and compatible)
        manifest = _initial_manifest(
            normalized_doc_id,
            pdf,
            output,
            source_sha=source_sha,
            source_size=source_size,
            page_count=page_count,
            dpi=dpi,
            model=selected_model,
            tile=tile,
            max_new_tokens=max_new_tokens,
            existing=existing if compatible else None,
        )
        if existing and not compatible:
            manifest.setdefault("warnings", []).append(
                "Existing OCR manifest did not match source PDF or DPI; OCR text will be regenerated."
            )
        write_json_atomic(manifest_path, manifest)

        log_path = output / "run_status.log"
        log_path.write_text(
            f"=== combined ingest+OCR run {manifest['created_at']} model={selected_model} pages={page_count} ===\n",
            encoding="utf-8",
        )

        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for page_number in range(1, page_count + 1):
            out_file = output / f"p{page_number:03d}.txt"
            page_meta = _page_entry(manifest, page_number, out_file.name)
            if can_resume and page_meta.get("status") != "error" and should_skip_existing(out_file):
                page_meta.update(_status_for_text(out_file, page_number, selected_model, "skipped"))
                _refresh_manifest(manifest)
                write_json_atomic(manifest_path, manifest)
                _append_log(log_path, f"[SKIP] {page_number:03d}/{page_count:03d} {out_file.name}")
                if progress:
                    progress("skip", page_number, page_count, out_file)
                continue

            start = time.time()
            try:
                page_meta.update({"status": "rendering", "updated_at": _utc_now(), "error": ""})
                write_json_atomic(manifest_path, manifest)

                page = document.load_page(page_number - 1)
                pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
                image_bgr = _pixmap_to_bgr(pix)
                page_meta.update(
                    {
                        "status": "ocr_running",
                        "width": pix.width,
                        "height": pix.height,
                        "updated_at": _utc_now(),
                    }
                )
                write_json_atomic(manifest_path, manifest)
                if progress:
                    progress("render", page_number, page_count, out_file)

                result = ocr_image_bgr(
                    image_bgr,
                    out_file,
                    image_name=f"p{page_number:03d}.png",
                    model=selected_model,
                    ollama_generate_url=selected_url,
                    prompt=prompt,
                    tile=tile,
                    max_new_tokens=max_new_tokens,
                    timeout_s=timeout_s,
                    debug_dir=output / "_debug" if debug else None,
                )
                page_meta.update(
                    _status_for_text(
                        out_file,
                        page_number,
                        selected_model,
                        "done",
                        result.elapsed_seconds,
                        result.tile_count,
                    )
                )
                page_meta.update({"width": pix.width, "height": pix.height, "debug_files": result.debug_files})
                _append_log(log_path, f"[OK ] {page_number:03d}/{page_count:03d} {out_file.name} ({result.elapsed_seconds}s) chars={len(result.text)}")
                if progress:
                    progress("done", page_number, page_count, out_file)
            except Exception as exc:
                out_file.write_text("[OCR_EMPTY]", encoding="utf-8")
                page_meta.update(_status_for_text(out_file, page_number, selected_model, "error", round(time.time() - start, 2), 0))
                page_meta["error"] = str(exc)
                if debug:
                    debug_dir = output / "_debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / f"p{page_number:03d}__error.txt").write_text(str(exc), encoding="utf-8")
                _append_log(log_path, f"[FAIL] {page_number:03d}/{page_count:03d} {out_file.name} {exc}")
                if progress:
                    progress("error", page_number, page_count, out_file)
            _refresh_manifest(manifest)
            write_json_atomic(manifest_path, manifest)

        _refresh_manifest(manifest)
        write_json_atomic(manifest_path, manifest)
        return manifest
    finally:
        document.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pixmap_to_bgr(pix: fitz.Pixmap) -> Any:
    samples = np.frombuffer(pix.samples, dtype=np.uint8)
    image = samples.reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if pix.n == 3:
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    if pix.n >= 4:
        return cv2.cvtColor(image[:, :, :4], cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2BGR)


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _compatible_manifest(
    existing: dict[str, Any],
    *,
    source_sha: str,
    page_count: int,
    dpi: int,
) -> bool:
    return (
        bool(existing)
        and existing.get("source_pdf_sha256") == source_sha
        and existing.get("page_count") == page_count
        and existing.get("dpi") == dpi
    )


def _initial_manifest(
    doc_id: str,
    pdf: Path,
    out_dir: Path,
    *,
    source_sha: str,
    source_size: int,
    page_count: int,
    dpi: int,
    model: str,
    tile: bool,
    max_new_tokens: int,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "doc_id": doc_id,
        "source_pdf": pdf.name,
        "source_pdf_path": str(pdf),
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": source_size,
        "out_dir": str(out_dir),
        "page_count": page_count,
        "total_pages": page_count,
        "dpi": dpi,
        "model": model,
        "tile": tile,
        "max_new_tokens": max_new_tokens,
        "image_storage": "not_persisted",
        "status": existing.get("status", "processing") if existing else "processing",
        "completed_pages": existing.get("completed_pages", 0) if existing else 0,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "warnings": list(existing.get("warnings", [])) if existing else [],
        "pages": list(existing.get("pages", [])) if existing else [],
    }


def _page_entry(manifest: dict[str, Any], page: int, filename: str) -> dict[str, Any]:
    for entry in manifest["pages"]:
        if entry.get("page") == page:
            entry["filename"] = filename
            return entry
    entry = {"page": page, "filename": filename, "status": "pending"}
    manifest["pages"].append(entry)
    manifest["pages"].sort(key=lambda item: int(item.get("page", 0)))
    return entry


def _status_for_text(
    out_file: Path,
    page: int,
    model: str,
    status: str,
    elapsed: float | str = "",
    tile_count: int = 0,
) -> dict[str, Any]:
    text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    return {
        "page": page,
        "filename": out_file.name,
        "text_file": out_file.name,
        "status": status,
        "char_count": len(text),
        "model": model,
        "tile_count": tile_count,
        "elapsed_seconds": elapsed,
        "updated_at": _utc_now(),
        "error": "",
    }


def _refresh_manifest(manifest: dict[str, Any]) -> None:
    completed = 0
    errors = 0
    for entry in manifest.get("pages", []):
        status = entry.get("status")
        if status in {"done", "skipped"}:
            completed += 1
        elif status == "error":
            errors += 1
    manifest["completed_pages"] = completed
    manifest["updated_at"] = _utc_now()
    total = int(manifest.get("total_pages") or manifest.get("page_count") or 0)
    if total and completed == total:
        manifest["status"] = "complete"
    elif errors:
        manifest["status"] = "partial_with_errors"
    else:
        manifest["status"] = "partial" if completed else "processing"


def _append_log(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
