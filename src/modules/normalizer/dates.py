from __future__ import annotations

import re
from typing import Optional

from shared.text_utils import normalize_ws


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

ISO_DATE_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def extract_doc_year(text: str) -> Optional[int]:
    match = re.search(r"\b(17|18|19|20)\d{2}\b", text or "")
    return int(match.group(0)) if match else None


def parse_day_month(text: str) -> tuple[int, int] | None:
    s = normalize_ws((text or "").lower().replace(",", " "))
    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b", s)
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2)
    for key, value in MONTHS.items():
        if month_name.startswith(key[:3]):
            return day, value
    return None


def _valid_day_month(day: int, month: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


def to_iso_date(text: str, doc_year: Optional[int] = None) -> tuple[str, str]:
    if not text:
        return "", ""
    s = normalize_ws(text)
    if ISO_DATE_PAT.match(s):
        return s, "explicit"

    match = re.search(r"(?:\bD/?\s*)?(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", s, flags=re.I)
    if match:
        day, month, raw_year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _valid_day_month(day, month):
            year = raw_year
            confidence = "explicit"
            if raw_year < 100:
                year = (doc_year // 100) * 100 + raw_year if doc_year else 1900 + raw_year
                confidence = "derived_from_doc"
            return f"{year:04d}-{month:02d}-{day:02d}", confidence

    match = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b", s)
    if match:
        month_name, day, year = match.group(1).lower(), int(match.group(2)), int(match.group(3))
        for key, value in MONTHS.items():
            if month_name.startswith(key[:3]) and _valid_day_month(day, value):
                return f"{year:04d}-{value:02d}-{day:02d}", "explicit"

    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b", s)
    if match:
        day, month_name, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        for key, value in MONTHS.items():
            if month_name.startswith(key[:3]) and _valid_day_month(day, value):
                return f"{year:04d}-{value:02d}-{day:02d}", "explicit"

    day_month = parse_day_month(s)
    if day_month and doc_year:
        day, month = day_month
        if _valid_day_month(day, month):
            return f"{doc_year:04d}-{month:02d}-{day:02d}", "derived_from_doc"

    return "", ""


def parse_first_date_in_text(text: str, doc_year: Optional[int] = None) -> tuple[str, str, str]:
    if not text:
        return "", "", ""
    s = normalize_ws(text)
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b(?:D/?\s*)?\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        r"\b[A-Za-z]+\s+\d{1,2},\s*\d{4}\b",
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, s, flags=re.I)
        if not match:
            continue
        raw = match.group(0)
        iso, confidence = to_iso_date(raw, doc_year)
        if iso:
            return iso, confidence, raw
    iso, confidence = to_iso_date(s, doc_year)
    return iso, confidence, s if iso else ""


def explain_date_parse(text: str, doc_year: Optional[int] = None) -> dict[str, object]:
    labels = [
        ("iso", r"\b\d{4}-\d{2}-\d{2}\b"),
        ("slash_or_dash", r"\b(?:D/?\s*)?\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
        ("month_day_year", r"\b[A-Za-z]+\s+\d{1,2},\s*\d{4}\b"),
        ("day_month", r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?\b"),
    ]
    tried = []
    s = normalize_ws(text or "")
    for label, pattern in labels:
        match = re.search(pattern, s, flags=re.I)
        raw = match.group(0) if match else ""
        iso, confidence = to_iso_date(raw, doc_year) if raw else ("", "")
        tried.append({"pattern": label, "matched": bool(raw), "raw": raw, "iso": iso, "confidence": confidence})
        if iso:
            return {"iso": iso, "confidence": confidence, "raw_matched": raw, "tried": tried}
    iso, confidence = to_iso_date(s, doc_year)
    return {"iso": iso, "confidence": confidence, "raw_matched": s if iso else "", "tried": tried}
