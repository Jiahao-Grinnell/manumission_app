from __future__ import annotations

from typing import Any

from modules.normalizer.dates import to_iso_date
from modules.normalizer.evidence import clean_evidence
from modules.normalizer.places import dedupe_place_rows, is_valid_place, normalize_place
from shared.text_utils import normalize_ws


DATE_CONFIDENCE_VALUES = {"explicit", "derived_from_doc", "unknown", ""}


def parse_candidate_places(obj: Any, name: str, page: int) -> list[dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in obj.get("places") or []:
        if not isinstance(item, dict):
            continue
        place = normalize_place(str(item.get("place") or ""))
        if not is_valid_place(place):
            continue
        time_text = _clean_text(item.get("time_text"))
        rows.append(
            {
                "Name": name,
                "Page": page,
                "Place": place,
                "Order": 0,
                "Arrival Date": "",
                "Date Confidence": "",
                "Time Info": time_text,
                "_evidence": clean_evidence(item.get("evidence")),
            }
        )
    return _strip_internal(dedupe_place_rows(rows, drop_internal=False))


def parse_place_rows(obj: Any, name: str, page: int, doc_year: int | None) -> list[dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in obj.get("places") or []:
        if not isinstance(item, dict):
            continue
        place = normalize_place(str(item.get("place") or ""))
        if not is_valid_place(place):
            continue
        order = _int_value(item.get("order"))
        raw_date = _clean_text(item.get("arrival_date"))
        arrival_date, inferred_conf = to_iso_date(raw_date, doc_year)
        date_confidence = _clean_text(item.get("date_confidence"))
        if date_confidence not in DATE_CONFIDENCE_VALUES:
            date_confidence = inferred_conf
        if not arrival_date:
            date_confidence = ""
        time_text = _clean_text(item.get("time_text"))
        if raw_date and not arrival_date and raw_date.casefold() not in time_text.casefold():
            time_text = normalize_ws(f"{raw_date}; {time_text}" if time_text else raw_date)
        rows.append(
            {
                "Name": name,
                "Page": page,
                "Place": place,
                "Order": max(order, 0),
                "Arrival Date": arrival_date,
                "Date Confidence": date_confidence,
                "Time Info": time_text,
                "_evidence": clean_evidence(item.get("evidence")),
            }
        )
    return _strip_internal(dedupe_place_rows(rows, drop_internal=False))


def serialize_place_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append(
            {
                "place": str(row.get("Place") or ""),
                "order": _int_value(row.get("Order")),
                "arrival_date": str(row.get("Arrival Date") or "") or None,
                "date_confidence": str(row.get("Date Confidence") or "") or None,
                "time_text": str(row.get("Time Info") or "") or None,
                "evidence": str(row.get("_evidence") or "") or None,
            }
        )
    return serialized


def _clean_text(value: Any) -> str:
    text = normalize_ws(str(value or ""))
    if text.casefold() in {"null", "none"}:
        return ""
    return text


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _strip_internal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("_position", None)
        item.pop("_promote", None)
        item.pop("_force_rank", None)
        cleaned.append(item)
    return cleaned
