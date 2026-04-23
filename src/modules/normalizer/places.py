from __future__ import annotations

import re
from typing import Any

from shared.text_utils import normalize_ws, strip_accents

from .vocabulary import PLACE_MAP, PLACE_STOPWORDS

PLACE_PROSE_MARKERS = {
    "without",
    "pressure",
    "slightest",
    "either",
    "would",
    "find",
    "way",
}


def normalize_place(place: str) -> str:
    if not place:
        return ""
    s = strip_accents(normalize_ws(str(place)))
    s = s.strip(" ,.;:[]{}\"'")
    s = re.sub(r"^\b(?:at|in|to|from|near|via)\b\s+", "", s, flags=re.I)
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = normalize_ws(s)
    low = re.sub(r"\s+", " ", s.lower().replace("-", " "))
    mapped = PLACE_MAP.get(low)
    if mapped:
        return mapped
    words = [word for word in s.split() if word]
    if not words:
        return ""
    out = [word.lower() if word.lower() in {"al", "ul", "el"} else word[:1].upper() + word[1:].lower() for word in words[:6]]
    return normalize_ws(" ".join(out))


def is_valid_place(place: str) -> bool:
    if not place:
        return False
    s = normalize_place(place)
    if not s or re.search(r"\d", s):
        return False
    low = s.lower()
    if low in PLACE_STOPWORDS or low in {"there", "here", "office", "agency", "residency"}:
        return False
    if len(s.split()) > 6:
        return False
    words = [word for word in re.findall(r"[a-z]+", low)]
    if words and words[0] in {"without", "if", "when"}:
        return False
    if len(words) >= 4 and any(word in PLACE_PROSE_MARKERS for word in words):
        return False
    return not bool(re.search(r"\b(h\.m\.s\.?|s\.s\.?|steamship|ship|dhow|vessel|boat)\b", low))


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def place_row_score(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    conf_rank = {"": 0, "unknown": 1, "derived_from_doc": 2, "explicit": 3}
    return (
        1 if _int_value(row.get("Order")) > 0 else 0,
        1 if row.get("Arrival Date") else 0,
        conf_rank.get(str(row.get("Date Confidence") or ""), 0),
        1 if row.get("Time Info") else 0,
        len(str(row.get("_evidence") or row.get("Evidence") or "")),
    )


def dedupe_place_rows(rows: list[dict[str, Any]], *, drop_internal: bool = True) -> list[dict[str, Any]]:
    if not rows:
        return []
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        candidate = dict(row)
        candidate["Name"] = str(candidate.get("Name") or candidate.get("name") or "")
        candidate["Place"] = normalize_place(str(candidate.get("Place") or candidate.get("place") or ""))
        if not candidate["Place"]:
            continue
        candidate["Order"] = _int_value(candidate.get("Order", candidate.get("order", 0)))
        key = (candidate["Name"].lower(), candidate["Place"].lower())
        current = best.get(key)
        if current is None or place_row_score(candidate) > place_row_score(current):
            merged = dict(candidate)
            if current is not None:
                _fill_place_row_gaps(merged, current)
            best[key] = merged
        else:
            _fill_place_row_gaps(current, candidate)

    positives = [row for row in best.values() if _int_value(row.get("Order")) > 0]
    zeroes = [row for row in best.values() if _int_value(row.get("Order")) == 0]
    positives.sort(key=lambda row: (_int_value(row.get("Order")), str(row.get("Arrival Date") or ""), row["Place"].lower()))
    for index, row in enumerate(positives, start=1):
        row["Order"] = index
    zeroes.sort(key=lambda row: (str(row.get("Arrival Date") or ""), row["Place"].lower()))
    out = positives + zeroes
    if drop_internal:
        for row in out:
            row.pop("_evidence", None)
            row.pop("_position", None)
            row.pop("_promote", None)
            row.pop("_force_rank", None)
    return out


def _fill_place_row_gaps(target: dict[str, Any], source: dict[str, Any]) -> None:
    if not target.get("Arrival Date") and source.get("Arrival Date"):
        target["Arrival Date"] = source["Arrival Date"]
        target["Date Confidence"] = source.get("Date Confidence", "")
    if not target.get("Time Info") and source.get("Time Info"):
        target["Time Info"] = source["Time Info"]
    if not target.get("_evidence") and source.get("_evidence"):
        target["_evidence"] = source["_evidence"]


def merge_place_date_enrichment(base_rows: list[dict[str, Any]], enriched_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not base_rows or not enriched_rows:
        return dedupe_place_rows(base_rows, drop_internal=False)
    by_place = {normalize_place(str(row.get("Place") or "")).lower(): dict(row) for row in base_rows}
    for row in enriched_rows:
        place = normalize_place(str(row.get("Place") or ""))
        target = by_place.get(place.lower())
        if not target:
            continue
        if not target.get("Arrival Date") and row.get("Arrival Date"):
            target["Arrival Date"] = row["Arrival Date"]
            target["Date Confidence"] = row.get("Date Confidence", "")
        if len(str(row.get("Time Info") or "")) > len(str(target.get("Time Info") or "")):
            target["Time Info"] = row["Time Info"]
    return dedupe_place_rows(list(by_place.values()), drop_internal=False)
