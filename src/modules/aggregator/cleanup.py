from __future__ import annotations

from collections import defaultdict
from typing import Any

from modules.normalizer.names import is_valid_name, names_maybe_same_person, normalize_name
from modules.normalizer.places import dedupe_place_rows, normalize_place


def cleanup_detail_rows(rows: list[dict[str, Any]], name_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    mapping = name_map or build_name_mapping([str(row.get("Name") or row.get("name") or "") for row in rows])
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        normalized = _normalize_detail_row(row, mapping)
        if not normalized["Name"]:
            continue
        key = (normalized["Name"].lower(), str(normalized["Page"]), normalized["Report Type"])
        if key in seen:
            continue
        cleaned.append(normalized)
        seen.add(key)
    return sorted(cleaned, key=lambda row: (_page_sort(row.get("Page")), row["Name"].lower()))


def cleanup_place_rows(rows: list[dict[str, Any]], name_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    mapping = name_map or build_name_mapping([str(row.get("Name") or row.get("name") or "") for row in rows])
    normalized_rows: list[dict[str, Any]] = []
    blank_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned = _normalize_place_row(row, mapping)
        if cleaned["Name"] and cleaned["Place"]:
            normalized_rows.append(cleaned)
        elif cleaned["Name"]:
            blank_rows.append(cleaned)

    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized_rows:
        by_page[_page_sort(row.get("Page"))].append(row)

    out: list[dict[str, Any]] = []
    for page in sorted(by_page):
        out.extend(dedupe_place_rows(by_page[page]))
    out.extend(blank_rows)
    return sorted(out, key=lambda row: (_page_sort(row.get("Page")), row["Name"].lower(), _order_sort(row.get("Order")), row["Place"].lower()))


def build_name_mapping(names: list[str]) -> dict[str, str]:
    items = [{"name": normalize_name(name), "evidence": ""} for name in names if is_valid_name(name)]
    clusters: list[list[dict[str, str]]] = []
    for item in items:
        for cluster in clusters:
            if any(names_maybe_same_person(item["name"], other["name"]) for other in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    mapping: dict[str, str] = {}
    for cluster in clusters:
        preferred = cluster[0]["name"]
        for item in cluster:
            mapping[normalize_name(item["name"]).lower()] = preferred
    return mapping


def cleanup_actions(names: list[str], name_map: dict[str, str]) -> list[str]:
    actions: list[str] = []
    grouped: dict[str, set[str]] = defaultdict(set)
    for name in names:
        norm = normalize_name(name)
        if not norm:
            continue
        grouped[name_map.get(norm.lower(), norm)].add(norm)
    for canonical, variants in sorted(grouped.items()):
        if len(variants) > 1:
            joined = ", ".join(sorted(variants))
            actions.append(f'Merged name variants into "{canonical}": {joined}')
    return actions


def _normalize_detail_row(row: dict[str, Any], name_map: dict[str, str]) -> dict[str, Any]:
    name = normalize_name(str(row.get("Name") or row.get("name") or ""))
    return {
        "Name": name_map.get(name.lower(), name),
        "Page": _page_sort(row.get("Page") or row.get("page")),
        "Report Type": _clean(row.get("Report Type") or row.get("report_type")),
        "Crime Type": _clean(row.get("Crime Type") or row.get("crime_type")),
        "Whether abuse": _clean(row.get("Whether abuse") or row.get("whether_abuse")),
        "Conflict Type": _clean(row.get("Conflict Type") or row.get("conflict_type")),
        "Trial": _clean(row.get("Trial") or row.get("trial")),
        "Amount paid": _clean(row.get("Amount paid") or row.get("amount_paid")),
    }


def _normalize_place_row(row: dict[str, Any], name_map: dict[str, str]) -> dict[str, Any]:
    name = normalize_name(str(row.get("Name") or row.get("name") or ""))
    return {
        "Name": name_map.get(name.lower(), name),
        "Page": _page_sort(row.get("Page") or row.get("page")),
        "Place": normalize_place(str(row.get("Place") or row.get("place") or "")),
        "Order": _order_sort(row.get("Order") if "Order" in row else row.get("order")),
        "Arrival Date": _clean(row.get("Arrival Date") or row.get("arrival_date")),
        "Date Confidence": _clean(row.get("Date Confidence") or row.get("date_confidence")),
        "Time Info": _clean(row.get("Time Info") or row.get("time_info")),
    }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "null"} else text


def _page_sort(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _order_sort(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
