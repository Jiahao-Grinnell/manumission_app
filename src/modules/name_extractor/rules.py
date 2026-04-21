from __future__ import annotations

import re
from typing import Any

from modules.normalizer.names import build_name_regex, normalize_name
from shared.text_utils import normalize_ws, strip_accents

from .merging import looks_like_candidate_name, merge_name_candidates


ROLE_NEGATIVE_PATTERNS = [
    ("sold_to_buyer", r"\bsold\s+(?:me\s+)?to\s+(?:one\s+)?{name}\b", 'matched "sold to {name}"'),
    ("bought_by_owner", r"\bbought\s+by\s+{name}\b", 'matched "bought by {name}"'),
    ("belonging_to_owner", r"\bbelonging\s+to\s+{name}\b", 'matched "belonging to {name}"'),
    ("owner_named", r"\bowner\s+(?:named\s+)?{name}\b", 'matched "owner {name}"'),
    ("master_named", r"\bmaster\s+(?:named\s+)?{name}\b", 'matched "master {name}"'),
    ("recorded_by", r"\bstatement\s+recorded\s+by\s+{name}\b", 'matched "statement recorded by {name}"'),
    ("letter_from", r"\bletter\s+from\s+{name}\b", 'matched "letter from {name}"'),
    ("signed_by", r"\bsigned\s+before\s+me\s+by\s+{name}\b", 'matched "signed ... by {name}"'),
]

ROLE_POSITIVE_PATTERNS = [
    ("statement_of_subject", r"\bstatement\s+of\s+(?:slave\s+)?{name}\b", 'matched "statement of {name}"'),
    ("statement_by_subject", r"\bstatement\s+made\s+by\s+{name}\b", 'matched "statement made by {name}"'),
    ("slave_name", r"\bslave\s+{name}\b", 'matched "slave {name}"'),
    ("named_slave_forward", r"\bslave\b.*?\bnamed\s+{name}\b", 'matched "slave ... named {name}"'),
    ("named_slave_reverse", r"\bnamed\s+{name}\b.*?\bslave\b", 'matched "named {name} ... slave"'),
    ("single_slave_named", r"\b1\s+slave\b.*?\bnamed\s+{name}\b", 'matched "1 slave ... named {name}"'),
    ("refugee_group", r"\brefugee\s+slaves?\b.*?\b{name}\b", 'matched "refugee slaves ... {name}"'),
    ("delivery_to", r"\bfor\s+delivery\s+to\s+{name}\b", 'matched "for delivery to {name}"'),
    ("grant_certificate", r"\bgrant\b.*?\bcertificate\b.*?\bto\s+{name}\b", 'matched "grant ... certificate ... to {name}"'),
    ("recommend_certificate", r"\brecommend\b.*?\bcertificate\b.*?\bfor\s+{name}\b", 'matched "recommend ... certificate ... for {name}"'),
    ("manumission_context", r"\bmanumission\b.*?\b{name}\b", 'matched "manumission ... {name}"'),
    ("free_status_context", r"\bfree\s+status\b.*?\b{name}\b", 'matched "free status ... {name}"'),
    ("repatriation_request", r"\b{name}\b.*?\brequests?\s+repatriation\b", 'matched "{name} requests repatriation"'),
]

STRONG_LOCAL_PHRASES = [
    "slave named",
    "refugee slave",
    "fugitive slave",
    "statement of slave",
    "statement made by",
    "grant certificate to",
    "recommend certificate for",
    "requests repatriation",
]

ROLE_TITLE_PATTERN = re.compile(
    r"\b(?:major|captain|shaikh|sheikh|secretary|agent|political\s+agent|resident|clerk|witness|signatory)\b",
    flags=re.I,
)


def normalize_for_match(text: str) -> str:
    normalized = strip_accents(normalize_ws(text)).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return normalize_ws(normalized)


def clean_evidence(value: Any, *, word_limit: int = 25) -> str:
    text = normalize_ws(str(value or ""))
    if not text:
        return ""
    return " ".join(text.split()[:word_limit])


def iter_name_contexts(name: str, ocr: str, window: int = 140) -> list[str]:
    pattern = build_name_regex(name)
    if not pattern or not ocr:
        return []
    contexts: list[str] = []
    for match in pattern.finditer(ocr):
        start = max(0, match.start() - window)
        end = min(len(ocr), match.end() + window)
        contexts.append(normalize_ws(ocr[start:end]))
    return contexts


def compile_name_phrase(pattern_template: str, name: str) -> re.Pattern[str]:
    tokens = [re.escape(token) for token in normalize_name(name).split() if token]
    joined = r"[\s,.;:'\"()\-]+".join(tokens) if tokens else r""
    return re.compile(pattern_template.format(name=joined), flags=re.I | re.S)


def _first_name_token(name: str) -> str:
    parts = normalize_name(name).split()
    return parts[0].lower() if parts else ""


def _pattern_hits(
    pattern_rows: list[tuple[str, str, str]],
    name: str,
    text: str,
    *,
    reason_type: str,
) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for key, template, reason in pattern_rows:
        match = compile_name_phrase(template, name).search(text)
        if match:
            hits.append(
                {
                    "key": key,
                    "reason_type": reason_type,
                    "reason": reason.replace("{name}", normalize_name(name)),
                    "excerpt": clean_evidence(match.group(0)),
                }
            )
    return hits


def positive_matches(name: str, text: str) -> list[dict[str, str]]:
    if not text:
        return []
    return _pattern_hits(ROLE_POSITIVE_PATTERNS, name, text, reason_type="positive_rule")


def negative_matches(name: str, text: str) -> list[dict[str, str]]:
    if not text:
        return []
    hits = _pattern_hits(ROLE_NEGATIVE_PATTERNS, name, text, reason_type="negative_rule")
    lower = text.lower()
    if re.search(r"\bfree\s*born\b", lower) and re.search(r"\bnot\s+a?\s*slave\b", lower):
        hits.append(
            {
                "key": "freeborn_not_slave",
                "reason_type": "freeborn_not_slave",
                "reason": 'matched "free born" plus "not a slave"',
                "excerpt": clean_evidence(text),
            }
        )
    first_token = _first_name_token(name)
    if first_token and ROLE_TITLE_PATTERN.search(lower) and re.search(rf"\b{re.escape(first_token)}\b", lower):
        hits.append(
            {
                "key": "official_title_context",
                "reason_type": "negative_rule",
                "reason": "appears in an official-title context",
                "excerpt": clean_evidence(text),
            }
        )
    return hits


def is_freeborn_not_slave_name(name: str, ocr: str) -> bool:
    return any(hit["reason_type"] == "freeborn_not_slave" for ctx in iter_name_contexts(name, ocr) for hit in negative_matches(name, ctx))


def explain_candidate_decision(name: str, evidence: str, ocr: str) -> dict[str, Any]:
    cleaned_name = normalize_name(name)
    cleaned_evidence = clean_evidence(evidence)
    texts = [text for text in [cleaned_evidence, *iter_name_contexts(cleaned_name, ocr)] if text]
    positive = [hit for text in texts for hit in positive_matches(cleaned_name, text)]
    negative = [hit for text in texts for hit in negative_matches(cleaned_name, text)]

    if any(hit["reason_type"] == "freeborn_not_slave" for hit in negative):
        first = next(hit for hit in negative if hit["reason_type"] == "freeborn_not_slave")
        return {
            "keep": False,
            "reason_type": first["reason_type"],
            "reason": first["reason"],
            "excerpt": first["excerpt"],
            "positive_matches": positive,
            "negative_matches": negative,
        }

    if positive:
        first = positive[0]
        return {
            "keep": True,
            "reason_type": first["reason_type"],
            "reason": first["reason"],
            "excerpt": first["excerpt"],
            "positive_matches": positive,
            "negative_matches": negative,
        }

    if negative:
        first = negative[0]
        return {
            "keep": False,
            "reason_type": first["reason_type"],
            "reason": first["reason"],
            "excerpt": first["excerpt"],
            "positive_matches": positive,
            "negative_matches": negative,
        }

    joined = " ".join(texts).lower()
    for phrase in STRONG_LOCAL_PHRASES:
        if phrase in joined:
            return {
                "keep": True,
                "reason_type": "strong_local_signal",
                "reason": f'found strong local phrase "{phrase}"',
                "excerpt": clean_evidence(joined),
                "positive_matches": positive,
                "negative_matches": negative,
            }

    if not looks_like_candidate_name(cleaned_name):
        return {
            "keep": False,
            "reason_type": "invalid_name",
            "reason": "Name did not pass basic validation.",
            "excerpt": cleaned_evidence,
            "positive_matches": positive,
            "negative_matches": negative,
        }

    return {
        "keep": False,
        "reason_type": "ambiguous_subject_role",
        "reason": "No positive subject signal survived the final rule filter.",
        "excerpt": cleaned_evidence or clean_evidence(ocr),
        "positive_matches": positive,
        "negative_matches": negative,
    }


def apply_rule_filter(named_people: list[dict[str, str]], ocr: str) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
    final_people: list[dict[str, str]] = []
    removed: list[dict[str, Any]] = []
    kept_reasons: list[dict[str, Any]] = []

    for item in merge_name_candidates(named_people):
        decision = explain_candidate_decision(item.get("name", ""), item.get("evidence", ""), ocr)
        normalized = {"name": normalize_name(item.get("name", "")), "evidence": clean_evidence(item.get("evidence", ""))}
        if decision["keep"]:
            final_people.append(normalized)
            kept_reasons.append(
                {
                    "name": normalized["name"],
                    "stage": "rule_filter",
                    "reason_type": decision["reason_type"],
                    "reason": decision["reason"],
                    "excerpt": decision["excerpt"],
                }
            )
            continue
        removed.append(
            {
                "name": normalized["name"],
                "evidence": normalized["evidence"],
                "stage": "rule_filter",
                "reason_type": decision["reason_type"],
                "reason": decision["reason"],
                "excerpt": decision["excerpt"],
            }
        )
    return final_people, removed, kept_reasons
