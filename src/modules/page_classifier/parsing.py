from __future__ import annotations

from typing import Any

from shared.schemas import PageDecision
from shared.text_utils import normalize_ws


REPORT_TYPES = {
    "statement",
    "correspondence",
}

LEGACY_REPORT_TYPE_MAP = {
    "transport/admin": "correspondence",
    "investigation/correspondence": "correspondence",
    "official correspondence": "correspondence",
}

SKIP_REASONS = {
    "index",
    "record_metadata",
    "bad_ocr",
}


def clean_evidence(text: Any) -> str:
    cleaned = normalize_ws(str(text or ""))
    if not cleaned:
        return ""
    return " ".join(cleaned.split()[:25])


def choose_report_type(value: str) -> str:
    normalized = normalize_ws(value)
    normalized = LEGACY_REPORT_TYPE_MAP.get(normalized.lower(), normalized)
    return normalized if normalized in REPORT_TYPES else "correspondence"


def parse_page_decision(obj: Any) -> PageDecision:
    if not isinstance(obj, dict):
        return PageDecision(should_extract=True, skip_reason=None, report_type="correspondence", evidence="")

    should_extract = bool(obj.get("should_extract", True))
    skip_reason = obj.get("skip_reason")
    if skip_reason in {None, "null"}:
        normalized_skip_reason = ""
    else:
        normalized_skip_reason = normalize_ws(str(skip_reason)).lower()
    if normalized_skip_reason not in SKIP_REASONS:
        normalized_skip_reason = ""

    report_type = choose_report_type(str(obj.get("report_type") or "correspondence"))
    evidence = clean_evidence(obj.get("evidence"))

    if normalized_skip_reason:
        return PageDecision(
            should_extract=False,
            skip_reason=normalized_skip_reason,  # type: ignore[arg-type]
            report_type=report_type,
            evidence=evidence,
        )

    return PageDecision(
        should_extract=should_extract,
        skip_reason=None,
        report_type=report_type,
        evidence=evidence,
    )
