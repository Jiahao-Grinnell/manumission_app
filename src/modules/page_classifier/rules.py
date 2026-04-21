from __future__ import annotations

import re
from typing import Any

from shared.text_utils import clean_ocr, normalize_ws, strip_accents

from .parsing import clean_evidence


CORRESPONDENCE_REPORT_PAT = re.compile(
    r"\b("
    r"repatriation|repatriate|passage|transport|taken\s+to|sent\s+to|for\s+delivery\s+to|"
    r"delivered\s+to|arrange(?:d)?\s+.*?\s+for|maintenance|subsistence|"
    r"provisions?\s+issued|victualled|accommodated\s+on\s+board|provision\s+account|"
    r"certificate\s+delivered|grant\s+certificate|manumission\s+certificate|"
    r"hand\s+him\s+over|made\s+over\s+to"
    r")\b",
    flags=re.I | re.S,
)

STATEMENT_REPORT_PAT = re.compile(
    r"\b(statement\s+of|statement\s+made\s+by|i\s+was\s+born|i\s+was\s+kidnapped|i\s+request)\b",
    flags=re.I,
)

INDEX_SKIP_PAT = re.compile(
    r"\b(index|contents|list\s+of\s+papers|table\s+of\s+contents)\b",
    flags=re.I,
)

RECORD_METADATA_SKIP_PAT = re.compile(
    r"\b("
    r"holding institution|about this record|view on the qatar digital library|"
    r"open government licence|reference:\s*ior/|copyright for this page|"
    r"written in english and arabic|extent and format"
    r")\b",
    flags=re.I,
)


def override_report_type_from_ocr(ocr: str, current: str) -> str:
    normalized = normalize_ws(clean_ocr(ocr))
    if STATEMENT_REPORT_PAT.search(normalized):
        return "statement"
    if CORRESPONDENCE_REPORT_PAT.search(normalized):
        return "correspondence"
    return current


def collect_rule_hints(ocr: str) -> dict[str, dict[str, Any]]:
    normalized = normalize_ws(clean_ocr(ocr))
    return {
        "statement_report": _pattern_hint(STATEMENT_REPORT_PAT, normalized, implied_value="statement"),
        "correspondence_report": _pattern_hint(CORRESPONDENCE_REPORT_PAT, normalized, implied_value="correspondence"),
        "index_skip_hint": _pattern_hint(INDEX_SKIP_PAT, normalized, implied_value="index"),
        "record_metadata_skip_hint": _pattern_hint(RECORD_METADATA_SKIP_PAT, normalized, implied_value="record_metadata"),
        "bad_ocr_skip_hint": _bad_ocr_hint(normalized),
    }


def explain_override(ocr: str, current: str) -> dict[str, Any]:
    hints = collect_rule_hints(ocr)
    final = current
    applied_by = None
    if hints["statement_report"]["matched"]:
        final = "statement"
        applied_by = "statement_report"
    elif hints["correspondence_report"]["matched"]:
        final = "correspondence"
        applied_by = "correspondence_report"
    return {
        "from": current,
        "to": final,
        "applied": final != current,
        "applied_by": applied_by,
        "rules": hints,
    }


def normalize_for_match(text: str) -> str:
    value = strip_accents(normalize_ws(text)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return normalize_ws(value)


def _pattern_hint(pattern: re.Pattern[str], text: str, *, implied_value: str) -> dict[str, Any]:
    match = pattern.search(text or "")
    return {
        "matched": bool(match),
        "excerpt": clean_evidence(match.group(0)) if match else "",
        "implied_value": implied_value,
    }


def _bad_ocr_hint(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    alpha_tokens = re.findall(r"[a-zA-Z]{2,}", stripped)
    noisy_chars = len(re.findall(r"[^a-zA-Z0-9\s]", stripped))
    length = len(stripped)
    matched = stripped in {"", "[OCR_EMPTY]"} or (length < 24 and len(alpha_tokens) < 4) or (length > 0 and noisy_chars > length * 0.4 and len(alpha_tokens) < 6)
    excerpt = clean_evidence(stripped) if matched else ""
    return {
        "matched": matched,
        "excerpt": excerpt,
        "implied_value": "bad_ocr",
    }
