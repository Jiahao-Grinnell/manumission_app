from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from shared.paths import normalize_doc_id
from shared.storage import read_json, write_json_atomic


ProgressCallback = Callable[[str, int, int, Path], None]


class PdfIngestError(RuntimeError):
    """Raised after the manifest has recorded a PDF ingest failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_filename(page_number: int) -> str:
    return f"p{page_number:03d}.png"


def _read_existing_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = read_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _initial_pages(page_count: int, existing: dict[str, Any] | None, out_dir: Path) -> list[dict[str, Any]]:
    existing_pages = {}
    if existing:
        for entry in existing.get("pages", []):
            if isinstance(entry, dict) and isinstance(entry.get("page"), int):
                existing_pages[entry["page"]] = dict(entry)

    pages: list[dict[str, Any]] = []
    for page_number in range(1, page_count + 1):
        filename = _page_filename(page_number)
        entry = existing_pages.get(page_number, {"page": page_number})
        entry["page"] = page_number
        entry["filename"] = filename
        entry.setdefault("status", "pending")

        image_path = out_dir / filename
        if entry.get("status") == "done" and (not image_path.exists() or image_path.stat().st_size <= 0):
            entry["status"] = "pending"
            entry.pop("width", None)
            entry.pop("height", None)
            entry.pop("size_bytes", None)

        pages.append(entry)
    return pages


def _completed_pages(manifest: dict[str, Any], out_dir: Path) -> int:
    completed = 0
    for entry in manifest.get("pages", []):
        if entry.get("status") != "done":
            continue
        image_path = out_dir / str(entry.get("filename", ""))
        if image_path.exists() and image_path.stat().st_size > 0:
            completed += 1
    return completed


def _set_final_status(manifest: dict[str, Any], out_dir: Path, had_error: bool = False) -> None:
    completed = _completed_pages(manifest, out_dir)
    manifest["completed_pages"] = completed
    if had_error:
        manifest["status"] = "error"
    elif completed == manifest.get("page_count"):
        manifest["status"] = "complete"
    else:
        manifest["status"] = "partial"
    manifest["updated_at"] = _utc_now()


def _compatible_manifest(
    existing: dict[str, Any] | None,
    *,
    source_sha: str,
    page_count: int,
    dpi: int,
) -> bool:
    if not existing:
        return False
    return (
        existing.get("source_pdf_sha256") == source_sha
        and existing.get("page_count") == page_count
        and existing.get("dpi") == dpi
    )


def _selected_page_numbers(page_count: int, start_page: int, end_page: int | None) -> range:
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    end = page_count if end_page is None else end_page
    if end < start_page:
        raise ValueError("end_page must be >= start_page")
    if end > page_count:
        raise ValueError(f"end_page {end} exceeds PDF page count {page_count}")
    return range(start_page, end + 1)


def ingest(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = 300,
    doc_id: str | None = None,
    start_page: int = 1,
    end_page: int | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Render a PDF into page PNGs and write an incremental manifest.

    Existing manifest entries marked ``done`` are skipped when the image file is
    still present, which makes interrupted ingests resumable by default.
    """

    pdf = Path(pdf_path)
    output = Path(out_dir)
    if not pdf.exists() or not pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {pdf}")
    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")

    normalized_doc_id = normalize_doc_id(doc_id or output.name or pdf.stem)
    output.mkdir(parents=True, exist_ok=True)

    source_sha = _sha256(pdf)
    source_size = pdf.stat().st_size
    manifest_path = output / "manifest.json"

    document = fitz.open(str(pdf))
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise PdfIngestError(f"Encrypted PDF cannot be opened without a password: {pdf}")

        page_count = document.page_count
        selected_pages = _selected_page_numbers(page_count, start_page, end_page)
        existing = _read_existing_manifest(manifest_path)
        compatible = _compatible_manifest(existing, source_sha=source_sha, page_count=page_count, dpi=dpi)
        now = _utc_now()
        warnings = list(existing.get("warnings", [])) if compatible and existing else []
        if existing and not compatible:
            warnings.append("Existing manifest did not match source PDF, page count, or DPI; rendering uses a new manifest.")

        manifest: dict[str, Any] = {
            "doc_id": normalized_doc_id,
            "source_pdf": pdf.name,
            "source_pdf_path": str(pdf),
            "source_pdf_sha256": source_sha,
            "source_pdf_size_bytes": source_size,
            "page_count": page_count,
            "dpi": dpi,
            "status": "processing",
            "completed_pages": 0,
            "created_at": existing.get("created_at", now) if compatible and existing else now,
            "updated_at": now,
            "warnings": warnings,
            "pages": _initial_pages(page_count, existing if compatible else None, output),
        }

        _set_final_status(manifest, output)
        if manifest["status"] != "complete":
            manifest["status"] = "processing"
        write_json_atomic(manifest_path, manifest)

        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)

        for page_number in selected_pages:
            page_meta = manifest["pages"][page_number - 1]
            page_file = output / _page_filename(page_number)
            if (
                not force
                and page_meta.get("status") == "done"
                and page_file.exists()
                and page_file.stat().st_size > 0
            ):
                if progress:
                    progress("skip", page_number, page_count, page_file)
                continue

            page_meta["status"] = "rendering"
            page_meta.pop("error", None)
            manifest["updated_at"] = _utc_now()
            write_json_atomic(manifest_path, manifest)

            try:
                page = document.load_page(page_number - 1)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                pix.save(str(page_file))
                page_meta.update(
                    {
                        "filename": page_file.name,
                        "width": pix.width,
                        "height": pix.height,
                        "size_bytes": page_file.stat().st_size,
                        "status": "done",
                        "rendered_at": _utc_now(),
                    }
                )
                _set_final_status(manifest, output)
                write_json_atomic(manifest_path, manifest)
                if progress:
                    progress("render", page_number, page_count, page_file)
            except Exception as exc:
                page_meta["status"] = "error"
                page_meta["error"] = str(exc)
                manifest.setdefault("warnings", []).append(f"Page {page_number}: {exc}")
                _set_final_status(manifest, output, had_error=True)
                write_json_atomic(manifest_path, manifest)
                if progress:
                    progress("error", page_number, page_count, page_file)
                raise PdfIngestError(f"Failed to render page {page_number} from {pdf}: {exc}") from exc

        _set_final_status(manifest, output)
        write_json_atomic(manifest_path, manifest)
        return manifest
    finally:
        document.close()
