from __future__ import annotations

from typing import Any

from modules.normalizer.places import is_valid_place


def verify_place_rows_need_retry(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return "Verifier returned no places."
    for item in validation_report(rows):
        if item["status"] == "fail":
            return item["message"]
    return None


def validation_report(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not rows:
        return [
            {"rule": "Positive orders", "status": "empty", "message": "No places extracted."},
            {"rule": "Duplicate places", "status": "empty", "message": "No places extracted."},
            {"rule": "Date confidence", "status": "empty", "message": "No places extracted."},
            {"rule": "Ascending dates", "status": "empty", "message": "No places extracted."},
            {"rule": "Valid place text", "status": "empty", "message": "No places extracted."},
        ]

    positive = [row for row in rows if _int_value(row.get("Order")) > 0]
    orders = [_int_value(row.get("Order")) for row in positive]
    duplicate_places = _duplicate_places(rows)
    bad_confidence = [
        str(row.get("Place") or "")
        for row in rows
        if not row.get("Arrival Date") and str(row.get("Date Confidence") or "")
    ]
    invalid_places = [str(row.get("Place") or "") for row in rows if not is_valid_place(str(row.get("Place") or ""))]
    ascending_ok = True
    dated_positive = [row for row in positive if row.get("Arrival Date")]
    for first, second in zip(dated_positive, dated_positive[1:]):
        if str(first.get("Arrival Date")) > str(second.get("Arrival Date")):
            ascending_ok = False
            break

    return [
        {
            "rule": "Positive orders",
            "status": "ok" if orders == list(range(1, len(orders) + 1)) else "fail",
            "message": "Positive orders form 1..n consecutively." if orders == list(range(1, len(orders) + 1)) else "Positive orders must be consecutive 1..n.",
        },
        {
            "rule": "Duplicate places",
            "status": "ok" if not duplicate_places else "fail",
            "message": "No duplicate final places remain." if not duplicate_places else f"Duplicate final places remain: {', '.join(duplicate_places)}.",
        },
        {
            "rule": "Date confidence",
            "status": "ok" if not bad_confidence else "fail",
            "message": "Date confidence is blank when arrival date is blank." if not bad_confidence else f"Date confidence must be blank when arrival_date is blank: {', '.join(bad_confidence)}.",
        },
        {
            "rule": "Ascending dates",
            "status": "ok" if ascending_ok else "fail",
            "message": "Positive route order is consistent with arrival dates." if ascending_ok else "Positive route order conflicts with arrival dates.",
        },
        {
            "rule": "Valid place text",
            "status": "ok" if not invalid_places else "fail",
            "message": "No ships or generic office words were kept." if not invalid_places else f"Invalid place-like text remains: {', '.join(invalid_places)}.",
        },
    ]


def _duplicate_places(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        place = str(row.get("Place") or "").strip().lower()
        if not place:
            continue
        if place in seen and place not in duplicates:
            duplicates.append(str(row.get("Place") or ""))
        seen.add(place)
    return duplicates


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
