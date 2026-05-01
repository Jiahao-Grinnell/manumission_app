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

SUBJECT_NAME_FRAGMENT = r"(?!(?:the|a|an|this|that|slave|negro|man|woman|boy|girl)\b)[a-z][a-z' -]{1,80}"
NAME_WORD = r"[A-Z][A-Za-z]*(?:['-][A-Za-z]+)*"
INITIALS = r"(?:[A-Z]\.){1,4}"
NAME_TOKEN = rf"(?:{INITIALS}|{NAME_WORD})"
NAME_CONNECTOR = r"(?i:bin|bint|ibn|ben|bu|abu|al|el|ul|us|son\s+of|daughter\s+of)"

STATEMENT_REPORT_PAT = re.compile(
    rf"\b("
    rf"statement\s+of\s+(?:slave\s+)?{SUBJECT_NAME_FRAGMENT}|"
    rf"statement\s+made\s+by\s+(?:slave\s+)?{SUBJECT_NAME_FRAGMENT}|"
    rf"i\s+was\s+born|i\s+was\s+kidnapped"
    rf")\b",
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

ADMIN_PUBLICITY_FORWARDING_PAT = re.compile(
    r"\b("
    r"two\s+copies\s+of\s+the\s+undermentioned\s+paper\s+are\s+forwarded|"
    r"undermentioned\s+paper\s+are\s+forwarded|"
    r"for\s+favour\s+of\s+giving\s+wide\s+publicity\s+to\s+the\s+proclamation|"
    r"wide\s+publicity\s+to\s+the\s+proclamation"
    r")\b",
    flags=re.I | re.S,
)

ADMIN_RECIPIENT_LIST_PAT = re.compile(
    r"\b(the\s+political\s+agent|his\s+majesty's\s+consul|british\s+vice-consul|british\s+consular\s+agent)\b",
    flags=re.I,
)

NAMED_SUBJECT_SIGNAL_PAT = re.compile(
    rf"\b("
    rf"statement\s+of\s+(?:slave\s+)?{SUBJECT_NAME_FRAGMENT}|"
    rf"statement\s+made\s+by\s+(?:slave\s+)?{SUBJECT_NAME_FRAGMENT}|"
    rf"slave\s+named\s+{SUBJECT_NAME_FRAGMENT}|"
    rf"named\s+{SUBJECT_NAME_FRAGMENT}.{{0,80}}\bslave\b|"
    rf"slaves?\s+whose\s+names?\s+are|"
    rf"following\s+(?:refugee\s+)?slaves?|"
    rf"certain\s+{SUBJECT_NAME_FRAGMENT}\s+(?:negro|slave)|"
    rf"grant\s+.*?\bcertificate\s+.*?\b(?:to|for)\s+{SUBJECT_NAME_FRAGMENT}|"
    rf"recommend\s+.*?\bcertificate\s+.*?\bfor\s+{SUBJECT_NAME_FRAGMENT}"
    rf")\b",
    flags=re.I | re.S,
)

PERSON_NAME_PATTERNS = [
    re.compile(rf"\b{NAME_TOKEN}\s+{NAME_CONNECTOR}\s+{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\b"),
    re.compile(rf"(?i:\b(?:Shaikh|Sheikh|Sayyid|Syed|Haji|Dr|Mr|Mrs|Major|Captain|Colonel|Lieutenant|Lt|Commodore)\.?)\s+{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\b"),
    re.compile(rf"(?i:\b(?:named|called|by\s+(?:the\s+)?name\s+of))\s+{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\b"),
    re.compile(rf"(?i:\bstatement\s+(?:of|made\s+by)\s+(?:slave\s+)?)\s*{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\b"),
    re.compile(rf"\b{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\s*,?\s+(?i:(?:the\s+)?(?:slave|negro|boy|girl|woman|man))\b"),
    re.compile(rf"(?i:\b(?:sd\.?|\(sd\.?\)|signed))\s*{NAME_TOKEN}(?:\s+(?:{NAME_CONNECTOR}\s+)?{NAME_TOKEN}){{0,4}}\b"),
    re.compile(rf"\b(?:{INITIALS}\s*)+{NAME_WORD}\b"),
    re.compile(rf"\b{NAME_WORD}\b.{{0,80}}\b(?i:states?|says|complains?|complained|has\s+come|came)\b.{{0,120}}\b(?i:slave|master|ill[- ]treated|cruelty|manumit|freedom)\b", flags=re.S),
]

FOOTER_PAT = re.compile(
    r"\b(?:reference:\s*ior/|copyright\s+for\s+this\s+page|view\s+on\s+the\s+qatar\s+digital\s+library)\b.*$",
    flags=re.I | re.S,
)
OFFICE_ONLY_LINE_PAT = re.compile(
    r"^\s*(?:to\s+)?(?:the\s+)?(?:political\s+agent|his\s+majesty's\s+consul|british\s+vice-consul|british\s+consular\s+agent|"
    r"secretary\s+to\s+the\s+political\s+resident|british\s+residency|political\s+agency|consulate\s+general)\b",
    flags=re.I,
)
NON_PERSON_EXCERPT_PAT = re.compile(
    r"\b(?:political\s+agent|political\s+resident|british\s+residency|consulate\s+general|government\s+licence|"
    r"qatar\s+digital\s+library|foreign\s+department|secretary\s+to|his\s+majesty|open\s+government)\b",
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
        "administrative_forwarding_skip_hint": _administrative_forwarding_hint(normalized),
        "bad_ocr_skip_hint": _bad_ocr_hint(normalized),
        "person_name_presence_hint": _person_name_presence_hint(normalized),
    }


def explain_skip_override(ocr: str) -> dict[str, Any]:
    hints = collect_rule_hints(ocr)
    if hints["person_name_presence_hint"]["matched"]:
        return {
            "should_skip": False,
            "skip_reason": None,
            "applied_by": "person_name_presence_hint",
            "evidence": hints["person_name_presence_hint"]["excerpt"],
            "rules": hints,
        }
    skip_reason = "record_metadata"
    if hints["bad_ocr_skip_hint"]["matched"]:
        skip_reason = "bad_ocr"
    elif hints["index_skip_hint"]["matched"]:
        skip_reason = "index"
    return {
        "should_skip": True,
        "skip_reason": skip_reason,
        "applied_by": "no_person_name_skip_hint",
        "evidence": "No visible personal name found on this page.",
        "rules": hints,
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


def _administrative_forwarding_hint(text: str) -> dict[str, Any]:
    admin_match = ADMIN_PUBLICITY_FORWARDING_PAT.search(text or "")
    has_recipient_list = bool(ADMIN_RECIPIENT_LIST_PAT.search(text or ""))
    has_named_subject = bool(NAMED_SUBJECT_SIGNAL_PAT.search(text or ""))
    matched = bool(admin_match and has_recipient_list and not has_named_subject)
    return {
        "matched": matched,
        "excerpt": clean_evidence(admin_match.group(0)) if admin_match else "",
        "implied_value": "record_metadata",
    }


def _person_name_presence_hint(text: str) -> dict[str, Any]:
    searchable = _name_search_text(text)
    for pattern in PERSON_NAME_PATTERNS:
        match = pattern.search(searchable)
        if match and not NON_PERSON_EXCERPT_PAT.search(match.group(0)):
            return {
                "matched": True,
                "excerpt": clean_evidence(match.group(0)),
                "implied_value": "extract",
            }
    return {
        "matched": False,
        "excerpt": "",
        "implied_value": "record_metadata",
    }


def _name_search_text(text: str) -> str:
    body = FOOTER_PAT.sub("", text or "")
    lines = []
    for line in body.splitlines():
        if OFFICE_ONLY_LINE_PAT.search(line):
            continue
        lines.append(line)
    return normalize_ws("\n".join(lines))
