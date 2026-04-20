from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.paths import doc_paths, normalize_doc_id
from shared.schemas import DETAIL_COLUMNS, PLACE_COLUMNS, STATUS_COLUMNS
from shared.storage import write_csv_atomic, write_json_atomic

from .cleanup import build_name_mapping, cleanup_actions, cleanup_detail_rows, cleanup_place_rows
from .stats import build_stats


@dataclass(frozen=True)
class AggregationResult:
    doc_id: str
    inter_dir: Path
    out_dir: Path
    detail_path: Path
    place_path: Path
    status_path: Path
    summary_path: Path
    stats: dict[str, Any]
    cleanup_actions: list[str] = field(default_factory=list)


def aggregate(
    doc_id: str | None = None,
    *,
    inter_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> AggregationResult:
    if doc_id:
        normalized_doc_id = normalize_doc_id(doc_id)
        paths = doc_paths(normalized_doc_id)
        input_dir = Path(inter_dir) if inter_dir else paths.inter_dir
        output_dir = Path(out_dir) if out_dir else paths.output_dir
    else:
        if inter_dir is None or out_dir is None:
            raise ValueError("Either doc_id or both inter_dir and out_dir are required")
        input_dir = Path(inter_dir)
        output_dir = Path(out_dir)
        normalized_doc_id = input_dir.name

    output_dir.mkdir(parents=True, exist_ok=True)

    detail_rows: list[dict[str, Any]] = []
    place_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []

    for page in _sorted_pages(input_dir):
        page_detail = _read_page_detail(input_dir, page)
        page_places = _read_page_places(input_dir, page)
        detail_rows.extend(page_detail)
        place_rows.extend(page_places)
        status_rows.append(_build_status_row(input_dir, page, page_detail, page_places))

    all_names = [str(row.get("Name") or row.get("name") or "") for row in detail_rows + place_rows]
    name_map = build_name_mapping(all_names)
    actions = cleanup_actions(all_names, name_map)
    cleaned_detail_rows = cleanup_detail_rows(detail_rows, name_map)
    cleaned_place_rows = cleanup_place_rows(place_rows, name_map)

    detail_path = output_dir / "Detailed info.csv"
    place_path = output_dir / "name place.csv"
    status_path = output_dir / "run_status.csv"
    summary_path = output_dir / "aggregation_summary.json"
    write_csv_atomic(detail_path, cleaned_detail_rows, DETAIL_COLUMNS)
    write_csv_atomic(place_path, cleaned_place_rows, PLACE_COLUMNS)
    write_csv_atomic(status_path, status_rows, STATUS_COLUMNS)
    stats = build_stats(cleaned_detail_rows, cleaned_place_rows, status_rows)
    write_json_atomic(summary_path, {"doc_id": normalized_doc_id, "stats": stats, "cleanup_actions": actions})

    return AggregationResult(
        doc_id=normalized_doc_id,
        inter_dir=input_dir,
        out_dir=output_dir,
        detail_path=detail_path,
        place_path=place_path,
        status_path=status_path,
        summary_path=summary_path,
        stats=stats,
        cleanup_actions=actions,
    )


def _sorted_pages(inter_dir: Path) -> list[int]:
    if not inter_dir.exists():
        return []
    pages = set()
    for path in inter_dir.glob("p*.json"):
        match = re.match(r"p(\d{3,})(?:\..+)?\.json$", path.name)
        if match:
            pages.add(int(match.group(1)))
    return sorted(pages)


def _read_json_optional(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _read_page_detail(inter_dir: Path, page: int) -> list[dict[str, Any]]:
    data = _read_json_optional(inter_dir / f"p{page:03d}.meta.json")
    rows = _extract_rows(data, keys=("rows", "detail_rows", "details"))
    for row in rows:
        row.setdefault("Page", page)
    return rows


def _read_page_places(inter_dir: Path, page: int) -> list[dict[str, Any]]:
    data = _read_json_optional(inter_dir / f"p{page:03d}.places.json")
    rows = _extract_rows(data, keys=("rows", "place_rows", "places"))
    if isinstance(data, dict):
        for person in data.get("people") or []:
            if not isinstance(person, dict):
                continue
            name = person.get("name") or person.get("Name") or ""
            person_rows = person.get("rows") or person.get("places") or []
            if person_rows:
                for row in person_rows:
                    if isinstance(row, dict):
                        item = dict(row)
                        item.setdefault("Name", name)
                        rows.append(item)
            elif name:
                rows.append({"Name": name, "Page": page, "Place": "", "Order": "", "Arrival Date": "", "Date Confidence": "", "Time Info": ""})
    for row in rows:
        row.setdefault("Page", page)
    return rows


def _extract_rows(data: Any, *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, dict)]
    return []


def _build_status_row(inter_dir: Path, page: int, detail_rows: list[dict[str, Any]], place_rows: list[dict[str, Any]]) -> dict[str, Any]:
    classify = _read_json_optional(inter_dir / f"p{page:03d}.classify.json")
    names = _read_json_optional(inter_dir / f"p{page:03d}.names.json")
    status = _page_status(classify, detail_rows, place_rows)
    named_people = _named_people_count(names, detail_rows)
    stats = _stats_from_json(classify, names)
    return {
        "page": page,
        "filename": f"p{page:03d}",
        "status": status,
        "named_people": named_people,
        "detail_rows": len(detail_rows),
        "place_rows": len(place_rows),
        "model_calls": stats.get("model_calls", 0),
        "repair_calls": stats.get("repair_calls", 0),
        "elapsed_seconds": stats.get("elapsed_seconds", ""),
        "note": _status_note(classify, names),
    }


def _page_status(classify: Any, detail_rows: list[dict[str, Any]], place_rows: list[dict[str, Any]]) -> str:
    if isinstance(classify, dict):
        explicit = classify.get("status")
        if explicit:
            return str(explicit)
        if classify.get("should_extract") is False:
            reason = classify.get("skip_reason") or "bad_ocr"
            return f"skip:{reason}"
    if detail_rows or place_rows:
        return "ok"
    return "no_named_people"


def _named_people_count(names: Any, detail_rows: list[dict[str, Any]]) -> int:
    if isinstance(names, dict):
        for key in ("named_people", "people", "rows"):
            value = names.get(key)
            if isinstance(value, list):
                return len(value)
    return len({str(row.get("Name") or row.get("name") or "").lower() for row in detail_rows if row.get("Name") or row.get("name")})


def _stats_from_json(*items: Any) -> dict[str, Any]:
    stats = {"model_calls": 0, "repair_calls": 0, "elapsed_seconds": ""}
    elapsed: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = item.get("stats") if isinstance(item.get("stats"), dict) else item
        stats["model_calls"] += _int_value(source.get("model_calls"))
        stats["repair_calls"] += _int_value(source.get("repair_calls"))
        if source.get("elapsed_seconds") not in {None, ""}:
            try:
                elapsed.append(float(source.get("elapsed_seconds")))
            except Exception:
                pass
    if elapsed:
        stats["elapsed_seconds"] = round(sum(elapsed), 2)
    return stats


def _status_note(classify: Any, names: Any) -> str:
    if isinstance(classify, dict):
        note = classify.get("note") or classify.get("evidence")
        if note:
            return str(note)
    if isinstance(names, dict) and names.get("note"):
        return str(names["note"])
    return ""


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
