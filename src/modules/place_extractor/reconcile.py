from __future__ import annotations

import re
from typing import Any

from modules.normalizer.dates import parse_first_date_in_text
from modules.normalizer.evidence import clean_evidence, normalize_for_match
from modules.normalizer.names import build_name_regex, normalize_name
from modules.normalizer.places import dedupe_place_rows, is_valid_place, normalize_place


CONFIDENT_ROUTE_PAT = re.compile(
    r"\b(arriv(?:e|ed|ing)|reached|escaped\s+to|took\s+refuge\s+at|went\s+to|came\s+to|brought\s+(?:me\s+)?to|taken\s+to|sent\s+to|forwarded\b|moved\s+to)\b",
    flags=re.I,
)
UNCERTAIN_ROUTE_PAT = re.compile(
    r"\b(request(?:ed)?|desired|wish(?:ed|es)?|intend(?:ed)?|propos(?:ed|es)|recommended|recommendation|delivery|certificate|office|agency|administrative|handling|not\s+clearly\s+completed)\b",
    flags=re.I,
)
ARRIVAL_DESTINATION_PAT = re.compile(
    r"arriv(?:ing|ed)\s+(?:at\s+|in\s+|to\s+)?([A-Za-z][A-Za-z' -]*[A-Za-z])(?=\s+(?:about\s+the|on|about)\b|[.,;\n])(?:\s+about\s+the\s+|\s+on\s+|\s+about\s+)?([^.,;\n]+)?",
    flags=re.I,
)
INFERRED_PLACE_PROSE_MARKERS = {"without", "pressure", "slightest", "either", "would", "find", "way"}


def first_text_position(snippet: str, ocr: str) -> int:
    if not snippet or not ocr:
        return 10**9
    index = ocr.casefold().find(snippet.casefold())
    if index >= 0:
        return index
    norm_snippet = normalize_for_match(snippet)
    norm_ocr = normalize_for_match(ocr)
    index = norm_ocr.find(norm_snippet)
    return index if index >= 0 else 10**9


def first_place_position(place: str, evidence: str, ocr: str) -> int:
    position = first_text_position(evidence, ocr)
    if position != 10**9:
        return position
    pattern = build_name_regex(place)
    if pattern:
        match = pattern.search(ocr)
        if match:
            return match.start()
    return first_text_position(place, ocr)


def is_uncertain_place_text(text: str) -> bool:
    return bool(UNCERTAIN_ROUTE_PAT.search(text or "")) and not bool(
        re.search(r"\b(arriv(?:ed|ing)|reached|escaped\s+to|went\s+to|came\s+to)\b", text or "", flags=re.I)
    )


def is_confident_place_text(text: str) -> bool:
    return bool(CONFIDENT_ROUTE_PAT.search(text or ""))


def infer_forwarding_transport_rows(name: str, ocr: str, page: int, doc_year: int | None) -> list[dict[str, Any]]:
    lower = ocr.casefold()
    if normalize_name(name).casefold() not in lower:
        return []
    rows: list[dict[str, Any]] = []
    src_match = re.search(r"^\s*from\s*-\s*.*?,\s*([A-Za-z][A-Za-z' -]+?)\.\s*$", ocr, flags=re.I | re.M)
    if not src_match:
        src_match = re.search(r"from\s*-\s*(?:.*?,\s*)?([A-Za-z][A-Za-z' -]+?)\.\s*$", ocr, flags=re.I | re.M)
    dst_match = ARRIVAL_DESTINATION_PAT.search(ocr)
    if not src_match or not dst_match:
        return rows

    src = normalize_place(src_match.group(1)) if src_match else ""
    dst = normalize_place(dst_match.group(1)) if dst_match else ""
    if not (_is_plausible_inferred_place(src) and _is_plausible_inferred_place(dst)):
        return rows

    if src:
        rows.append(
            {
                "Name": name,
                "Page": page,
                "Place": src,
                "Order": 1,
                "Arrival Date": "",
                "Date Confidence": "",
                "Time Info": "",
                "_evidence": clean_evidence(src_match.group(0) if src_match else src),
            }
        )

    if dst:
        arrival_date = ""
        date_confidence = ""
        time_text = ""
        if dst_match and dst_match.group(2):
            arrival_date, date_confidence, time_text = parse_first_date_in_text(dst_match.group(2), doc_year)
            if not arrival_date:
                time_text = re.sub(r"\s+", " ", dst_match.group(2)).strip()
        rows.append(
            {
                "Name": name,
                "Page": page,
                "Place": dst,
                "Order": 2 if src else 1,
                "Arrival Date": arrival_date,
                "Date Confidence": date_confidence,
                "Time Info": time_text,
                "_evidence": clean_evidence(dst_match.group(0) if dst_match else dst),
            }
        )
    return rows


def reconcile_place_rows(rows: list[dict[str, Any]], ocr: str, name: str, page: int, doc_year: int | None) -> list[dict[str, Any]]:
    work = dedupe_place_rows([dict(row) for row in rows], drop_internal=False)
    if not work:
        return []
    if any(_int_value(row.get("Order")) > 0 for row in work):
        return _preserve_existing_route(work, ocr)

    work.extend(infer_forwarding_transport_rows(name, ocr, page, doc_year))
    work = dedupe_place_rows(work, drop_internal=False)
    if not work:
        return []

    _annotate_positions(work, ocr)
    for row in work:
        text = _row_context(row)
        order = _int_value(row.get("Order"))
        if order <= 0 and row.get("Arrival Date") and not is_uncertain_place_text(text):
            row["_promote"] = True
        elif order <= 0 and _can_promote_from_text(row, text):
            row["_promote"] = True
        else:
            row["_promote"] = False

    route_rows: list[dict[str, Any]] = []
    zero_rows: list[dict[str, Any]] = []
    for row in work:
        order = _int_value(row.get("Order"))
        if order > 0 or row.get("_promote"):
            route_rows.append(row)
        else:
            zero_rows.append(row)

    route_rows.sort(
        key=lambda row: (
            _int_value(row.get("_position")) or 10**9,
            str(row.get("Place") or "").casefold(),
        )
    )
    for index, row in enumerate(route_rows, start=1):
        row["Order"] = index

    zero_rows.sort(key=lambda row: (_int_value(row.get("_position")) or 10**9, str(row.get("Place") or "").casefold()))
    for row in zero_rows:
        row["Order"] = 0

    return _strip_route_internal(route_rows + zero_rows)


def _preserve_existing_route(rows: list[dict[str, Any]], ocr: str) -> list[dict[str, Any]]:
    work = [dict(row) for row in rows]
    _annotate_positions(work, ocr)
    route_rows = [row for row in work if _int_value(row.get("Order")) > 0]
    zero_rows = [row for row in work if _int_value(row.get("Order")) <= 0]
    route_rows.sort(key=lambda row: (_int_value(row.get("Order")), _int_value(row.get("_position")) or 10**9, str(row.get("Place") or "").casefold()))
    zero_rows.sort(key=lambda row: (_int_value(row.get("_position")) or 10**9, str(row.get("Place") or "").casefold()))
    for row in zero_rows:
        row["Order"] = 0
    return _strip_route_internal(route_rows + zero_rows)


def _annotate_positions(rows: list[dict[str, Any]], ocr: str) -> None:
    for row in rows:
        row["_position"] = first_place_position(str(row.get("Place") or ""), _row_context(row), ocr)


def _row_context(row: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", f"{row.get('_evidence', '')} {row.get('Time Info', '')}").strip()


def _can_promote_from_text(row: dict[str, Any], text: str) -> bool:
    return is_valid_place(str(row.get("Place") or "")) and is_confident_place_text(text) and not is_uncertain_place_text(text)


def _is_plausible_inferred_place(place: str) -> bool:
    if not is_valid_place(place):
        return False
    words = re.findall(r"[a-z]+", place.casefold())
    if not words or len(words) > 4:
        return False
    return not any(word in INFERRED_PLACE_PROSE_MARKERS for word in words)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _strip_route_internal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("_position", None)
        item.pop("_promote", None)
        item.pop("_force_rank", None)
        cleaned.append(item)
    return cleaned
