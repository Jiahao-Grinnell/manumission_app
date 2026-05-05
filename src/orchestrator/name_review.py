from __future__ import annotations

import csv
import io
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from shared.paths import DocumentPaths, doc_paths
from shared.storage import read_json, write_csv_atomic, write_json_atomic


COMBINED_TEXT_FILENAME = "name_review_combined_text.txt"
NAMES_CSV_FILENAME = "name_review_names.csv"
UPLOADED_CSV_FILENAME = "name_review_uploaded_names.csv"
CSV_COLUMNS = ["Name", "Page"]


def name_review_file_map(doc_id: str, *, paths: DocumentPaths | Any | None = None) -> dict[str, Path]:
    resolved = paths or doc_paths(doc_id)
    output_dir = Path(resolved.output_dir)
    return {
        "name_review_text": output_dir / COMBINED_TEXT_FILENAME,
        "name_review_csv": output_dir / NAMES_CSV_FILENAME,
        "name_review_uploaded_csv": output_dir / UPLOADED_CSV_FILENAME,
    }


def generate_name_review_artifacts(doc_id: str, *, paths: DocumentPaths | Any | None = None) -> dict[str, Any]:
    resolved = paths or doc_paths(doc_id)
    file_map = name_review_file_map(doc_id, paths=resolved)
    names_pages = _names_pages(Path(resolved.inter_dir))
    text_blocks: list[str] = []
    rows: list[dict[str, Any]] = []

    for page, names_path in names_pages:
        ocr_path = Path(resolved.ocr_text(page))
        ocr_text = ocr_path.read_text(encoding="utf-8", errors="replace").rstrip() if ocr_path.exists() else ""
        text_blocks.append(f"========== PAGE {page:03d} ==========\n{ocr_text}")
        for person in _load_named_people(names_path):
            rows.append({"Name": person["name"], "Page": page})

    combined = "\n\n".join(text_blocks)
    if combined:
        combined += "\n"
    _write_text_atomic(file_map["name_review_text"], combined)
    write_csv_atomic(file_map["name_review_csv"], rows, CSV_COLUMNS)

    return {
        "page_count": len(names_pages),
        "name_rows": len(rows),
        "combined_text": _file_info(file_map["name_review_text"]),
        "names_csv": _file_info(file_map["name_review_csv"]),
    }


def parse_name_page_csv(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    for line_number, raw_row in enumerate(reader, start=1):
        if not raw_row or all(not str(cell).strip() for cell in raw_row):
            continue
        if line_number == 1 and len(raw_row) >= 2 and raw_row[0].strip().casefold() == "name" and raw_row[1].strip().casefold() == "page":
            continue
        if len(raw_row) < 2:
            raise ValueError(f"CSV line {line_number} must contain Name and Page")
        name = str(raw_row[0] or "").strip()
        raw_page = str(raw_row[1] or "").strip()
        if not name:
            continue
        page = _parse_page(raw_page, line_number=line_number)
        rows.append({"Name": name, "Page": page})
    return rows


def apply_corrected_names(
    doc_id: str,
    rows: list[dict[str, Any]],
    *,
    paths: DocumentPaths | Any | None = None,
) -> dict[str, Any]:
    resolved = paths or doc_paths(doc_id)
    file_map = name_review_file_map(doc_id, paths=resolved)
    normalized_rows = _dedupe_rows(rows)
    write_csv_atomic(file_map["name_review_uploaded_csv"], normalized_rows, CSV_COLUMNS)

    grouped: dict[int, list[str]] = defaultdict(list)
    for row in normalized_rows:
        grouped[int(row["Page"])].append(str(row["Name"]))

    existing_pages = {page for page, _ in _names_pages(Path(resolved.inter_dir))}
    target_pages = sorted(existing_pages | set(grouped.keys()))
    for page in target_pages:
        ocr_path = Path(resolved.ocr_text(page))
        if not ocr_path.exists():
            raise ValueError(f"Cannot apply corrected names for page {page}: OCR text is missing")
        _ensure_classify_extractable(resolved, page, has_names=bool(grouped.get(page)))
        _write_corrected_names_json(resolved, page, grouped.get(page, []))

    cleared = _clear_downstream_artifacts(resolved)
    return {
        "uploaded_csv": _file_info(file_map["name_review_uploaded_csv"]),
        "page_count": len(target_pages),
        "name_rows": len(normalized_rows),
        "cleared_downstream": cleared,
    }


def _names_pages(inter_dir: Path) -> list[tuple[int, Path]]:
    pages: list[tuple[int, Path]] = []
    for path in sorted(inter_dir.glob("p*.names.json")):
        if path.is_file():
            page = _page_from_path(path)
            if page > 0:
                pages.append((page, path))
    return pages


def _load_named_people(path: Path) -> list[dict[str, str]]:
    record = _read_json_dict(path)
    people: list[dict[str, str]] = []
    raw = record.get("named_people") if isinstance(record, dict) else None
    if not isinstance(raw, list):
        return people
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        people.append({"name": name, "evidence": str(item.get("evidence") or "")})
    return people


def _ensure_classify_extractable(paths: DocumentPaths | Any, page: int, *, has_names: bool) -> None:
    classify_path = Path(paths.classify(page))
    record = _read_json_dict(classify_path)
    if not has_names:
        return
    updated = dict(record)
    updated["page"] = int(updated.get("page") or page)
    updated["should_extract"] = True
    updated["skip_reason"] = None
    updated["report_type"] = str(updated.get("report_type") or "statement")
    updated["name_review_override"] = True
    write_json_atomic(classify_path, updated)


def _write_corrected_names_json(paths: DocumentPaths | Any, page: int, names: list[str]) -> None:
    names_path = Path(paths.names(page))
    existing = _read_json_dict(names_path)
    named_people = [{"name": name, "evidence": "Accepted from name review CSV."} for name in names]
    payload = dict(existing)
    payload.update(
        {
            "page": int(existing.get("page") or page),
            "report_type": str(existing.get("report_type") or ""),
            "named_people": named_people,
            "name_review_override": True,
            "name_review_source": "uploaded_csv",
        }
    )
    passes = dict(payload.get("passes") or {})
    passes["name_review"] = {
        "stage": "name_review",
        "source": "uploaded_csv",
        "names": named_people,
    }
    payload["passes"] = passes
    write_json_atomic(names_path, payload)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("Name") or row.get("name") or "").strip()
        page = int(row.get("Page") or row.get("page") or 0)
        if not name or page <= 0:
            continue
        key = (page, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append({"Name": name, "Page": page})
    return sorted(result, key=lambda item: (int(item["Page"]), str(item["Name"]).casefold()))


def _clear_downstream_artifacts(paths: DocumentPaths | Any) -> dict[str, int]:
    counts = {"meta": 0, "places": 0, "outputs": 0}
    inter_dir = Path(paths.inter_dir)
    for pattern, key in (("p*.meta.json", "meta"), ("p*.places.json", "places")):
        for path in inter_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                counts[key] += 1
    for filename in ("Detailed info.csv", "name place.csv", "run_status.csv", "aggregation_summary.json"):
        path = Path(paths.output_dir) / filename
        if path.exists() and path.is_file():
            path.unlink()
            counts["outputs"] += 1
    return counts


def _parse_page(raw_page: str, *, line_number: int) -> int:
    cleaned = raw_page.strip().lower()
    if cleaned.startswith("p"):
        cleaned = cleaned[1:]
    try:
        page = int(cleaned)
    except ValueError as exc:
        raise ValueError(f"CSV line {line_number} has invalid Page value: {raw_page}") from exc
    if page <= 0:
        raise ValueError(f"CSV line {line_number} Page must be positive")
    return page


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def _file_info(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _page_from_path(path: Path) -> int:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return int(digits) if digits else 0
