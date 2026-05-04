from __future__ import annotations

import re
from typing import Any

from modules.normalizer.names import build_name_regex, normalize_name
from shared.text_utils import normalize_ws, strip_accents

from .merging import (
    canonicalize_candidate_name,
    is_generic_subject_phrase,
    looks_like_candidate_name,
    looks_like_subject_label,
    merge_name_candidates,
)


ROLE_NEGATIVE_PATTERNS = [
    ("sold_to_buyer", r"\bsold\s+(?:me\s+)?to\s+(?:one\s+)?{name}\b", 'matched "sold to {name}"'),
    ("sold_by_seller", r"\bsold\s+by\s+{name}\b", 'matched "sold by {name}"'),
    ("sold_through_broker", r"\bsold\s+through\s+{name}\b", 'matched "sold through {name}"'),
    ("bought_by_owner", r"\bbought\s+by\s+{name}\b", 'matched "bought by {name}"'),
    ("belonging_to_owner", r"\bbelonging\s+to\s+{name}\b", 'matched "belonging to {name}"'),
    ("slave_of_man_named_owner", r"\bslave\s+of\s+(?:a\s+)?man\s+named\s+{name}\b", 'matched "slave of a man named {name}"'),
    ("owner_named", r"\bowner\s+(?:named\s+)?{name}\b", 'matched "owner {name}"'),
    ("master_named", r"\bmaster\s+(?:named\s+)?{name}\b", 'matched "master {name}"'),
    ("makhudha_named", r"\bma?khudha\s+(?:named\s+)?{name}\b", 'matched "makhudha named {name}"'),
    ("kidnapped_by", r"\bkidnapped\s+by\s+{name}\b", 'matched "kidnapped by {name}"'),
    ("captured_by", r"\bcaptured\s+by\s+{name}\b", 'matched "captured by {name}"'),
    ("slave_broker", r"\b{name}\b.{{0,80}}\bslave\s+broker\b", 'matched "{name} ... slave broker"'),
    ("nakhuda_named", r"\bnakhuda\s+(?:by\s+name\s+|named\s+){name}\b", 'matched "Nakhuda named {name}"'),
    ("nakhuda_title", r"\bnakhuda\s+{name}\b", 'matched "Nakhuda {name}"'),
    ("papers_from", r"\b(?:manumission\s+)?papers?\s+from\s+{name}\b", 'matched "papers from {name}"'),
    ("take_papers_from", r"\btake\b.{{0,80}}\bpapers?\s+from\s+{name}\b", 'matched "take ... papers from {name}"'),
    ("witness_list", r"\b(?:witnesses|names\s+of\s+people\s+who\s+know\s+him)\b.{{0,180}}\b{name}\b", 'matched witness-list context'),
    ("witness_person_list", r"\b(?:two|three|four|\d+)\s+persons?\b.{{0,180}}\b{name}\b.{{0,240}}\b(?:witness|proof|know|statement)\b", 'matched witness/persons-who-know context'),
    ("relation_witness_context", r"\b(?:witness(?:es)?|proof|people\s+who\s+know|persons?\s+who\s+know)\b.{{0,180}}\b{name}\b", 'matched relation witness/proof context'),
    ("family_only_named", r"\b(?:i\s+have|has|had)\s+(?:a\s+)?(?:son|daughter|wife|child)\b.{{0,100}}\bnamed\s+{name}\b", 'matched family-only named relative context'),
    ("servant_named", r"\bservant\b.{{0,80}}\bnamed\s+{name}\b", 'matched "servant named {name}"'),
    ("named_not_slave", r"\bnamed\s+{name}\b.{{0,140}}\bnot\s+a\s+slave\b", 'matched "named {name} ... not a slave"'),
    ("original_name_alias", r"\boriginal\s+name\s+{name}\b", 'matched "original name {name}" alias context'),
    ("slave_traffic_list", r"\bmen\s+engaged\s+in\s+slave\s+traffic\b.{{0,320}}\b{name}\b", 'matched slave-traffic list context'),
    ("recorded_by", r"\bstatement\s+recorded\s+by\s+{name}\b", 'matched "statement recorded by {name}"'),
    ("letter_from", r"\bletter\s+from\s+{name}\b", 'matched "letter from {name}"'),
    ("signed_by", r"\bsigned\s+before\s+me\s+by\s+{name}\b", 'matched "signed ... by {name}"'),
]

ROLE_POSITIVE_PATTERNS = [
    ("statement_of_subject", r"\bstatement\s+of\s+(?:slave\s+)?{name}\b", 'matched "statement of {name}"'),
    ("statement_by_subject", r"\bstatement\s+made\s+by\s+{name}\b", 'matched "statement made by {name}"'),
    ("slave_name", r"\bslave\s+{name}\b", 'matched "slave {name}"'),
    ("negro_name", r"\bnegro\s+{name}\b", 'matched "negro {name}"'),
    ("case_of_negro", r"\bcase\s+of\s+(?:the\s+)?(?:negro|slave)\s+{name}\b", 'matched "case of the negro {name}"'),
    ("subject_slave_of_owner", r"\b{name}\b\s*,?\s+(?:the\s+)?(?:slave|negro)\s+of\b", 'matched "{name} slave of ..."'),
    ("named_negro_by_name", r"\b(?:slave|negro|boy|woman)\b.{{0,40}}\b(?:by\s+(?:the\s+)?name\s+of|by\s+name|named)\s+{name}\b", 'matched "slave/negro/boy/woman by name {name}"'),
    ("name_field_subject_direct", r"\bName\.?\s*{name}\b.{{0,520}}\b(?:stolen|sold|slave|master|complain|manumit|buy\s+me|keep\s+me)\b", 'matched "Name. {name}" with subject details'),
    ("name_by_name", r"\b{name}\b\s*,?\s+by\s+name\b", 'matched "{name} by name"'),
    ("name_slave_appositive", r"\b{name}\b\s*,?\s+(?:the\s+)?slave\b", 'matched "{name}, the slave"'),
    ("name_states_slave", r"\b{name}\b.{{0,100}}\bstates?\b.{{0,100}}\bslave\b", 'matched "{name} states ... slave"'),
    ("woman_named_subject", r"\bwoman\s+(?:named\s+)?{name}\b.{{0,180}}\b(?:kept\s+as\s+a\s+slave|slave|kidnapped|sold|freedom|manumission|examin(?:e|ation))\b", 'matched "woman {name} ... slave/freedom"'),
    (
        "manumission_certificate_desired",
        r"\b{name}\b.{{0,120}}\b(?:desires?|wants?|requests?)\b.{{0,80}}\bmanumission\s+certificate\b",
        'matched "{name} ... manumission certificate"',
    ),
    ("named_slave_forward", r"\bslave\b.*?\bnamed\s+{name}\b", 'matched "slave ... named {name}"'),
    ("named_slave_reverse", r"\bnamed\s+{name}\b.*?\bslave\b", 'matched "named {name} ... slave"'),
    ("single_slave_named", r"\b1\s+slave\b.*?\bnamed\s+{name}\b", 'matched "1 slave ... named {name}"'),
    ("certain_negro_claims", r"\bcertain\s+{name}\s+(?:negro|slave)\b.{{0,100}}\bclaims?\s+to\s+have\s+been\s+(?:a\s+)?slave\b", 'matched "certain {name} negro ... slave"'),
    ("plural_slave_names", r"\bslaves?\s+whose\s+names?\s+are\b.{{0,260}}\b{name}\b", 'matched "slaves whose names are ... {name}"'),
    ("following_slave_names", r"\bfollowing\s+(?:refugee\s+)?slaves?\b.{{0,260}}\b{name}\b", 'matched "following slaves ... {name}"'),
    ("refugee_group", r"\brefugee\s+slaves?\b.*?\b{name}\b", 'matched "refugee slaves ... {name}"'),
    ("delivery_to", r"\bfor\s+delivery\s+to\s+{name}\b", 'matched "for delivery to {name}"'),
    ("grant_certificate", r"\bgrant\b.*?\bcertificate\b.*?\bto\s+{name}\b", 'matched "grant ... certificate ... to {name}"'),
    ("recommend_certificate", r"\brecommend\b.*?\bcertificate\b.*?\bfor\s+{name}\b", 'matched "recommend ... certificate ... for {name}"'),
    ("free_status_context", r"\bfree\s+status\b.*?\b{name}\b", 'matched "free status ... {name}"'),
    ("repatriation_request", r"\b{name}\b.*?\brequests?\s+repatriation\b", 'matched "{name} requests repatriation"'),
]

HARD_NEGATIVE_KEYS = {
    "sold_to_buyer",
    "sold_by_seller",
    "sold_through_broker",
    "bought_by_owner",
    "belonging_to_owner",
    "slave_of_man_named_owner",
    "owner_named",
    "master_named",
    "makhudha_named",
    "kidnapped_by",
    "captured_by",
    "slave_broker",
    "nakhuda_named",
    "nakhuda_title",
    "papers_from",
    "take_papers_from",
    "witness_list",
    "witness_person_list",
    "relation_witness_context",
    "family_only_named",
    "servant_named",
    "named_not_slave",
    "original_name_alias",
    "slave_traffic_list",
    "recorded_by",
    "letter_from",
    "signed_by",
    "official_title_context",
}

STRONG_LOCAL_PHRASES = [
    "slave named",
    "refugee slave",
    "fugitive slave",
    "statement of slave",
    "statement made by",
    "slaves whose names are",
    "whose names are",
    "case of the negro",
    "grant certificate to",
    "recommend certificate for",
    "requests repatriation",
]

ROLE_TITLE_PATTERN = re.compile(
    r"\b(?:major|captain|shaikh|sheikh|secretary|agent|political\s+agent|resident|clerk|witness|signatory)\b",
    flags=re.I,
)

TITLE_BEFORE_NAME_TEMPLATE = r"\b(?:major|captain|shaikh|sheikh|secretary|agent|political\s+agent|resident|clerk|witness|signatory)\s+{name}\b"
PLURAL_SUBJECT_LIST_PAT = re.compile(
    r"\b(?:\w+\s+)?slaves?\s+whose\s+names?\s+are\s*:?\s*(?P<body>.*?)(?=\n\s*\n|and\s+who\s+belong|these\s+slaves\b|i\s+request\b|$)",
    flags=re.I | re.S,
)
NUMBERED_NAME_PAT = re.compile(r"(?:^|[\r\n])\s*\d+[\).]?\s*([A-Z][A-Za-z' -]*?[A-Za-z])(?=[.;\r\n])")
CERTAIN_NEGRO_PAT = re.compile(
    r"\bcertain\s+([A-Z][A-Za-z' -]*?[A-Za-z])\s+(?:negro|slave)\b.{0,100}\bclaims?\s+to\s+have\s+been\s+(?:a\s+)?slave\b",
    flags=re.I | re.S,
)
SUBJECT_NAMED_PAT = re.compile(
    r"\b(?:slave|negro|boy|woman)\b.{0,40}\b(?:by\s+(?:the\s+)?name\s+of|by\s+name|named)\s+"
    r"([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4})\b",
    flags=re.I | re.S,
)
REVERSE_BY_NAME_PAT = re.compile(
    r"\b(?:slave|negro|man|boy|woman)\b.{0,40}?\b"
    r"([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4})"
    r"\s*,?\s+by\s+name\b",
    flags=re.I | re.S,
)
STATEMENT_OF_NAME_PAT = re.compile(
    r"\bstatement\s+of\s+(?:this\s+)?(?:slave\s+)?"
    r"([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|son|daughter|of|Abu|Umm)\s+[A-Z][A-Za-z']+){0,6})"
    r"(?=\s*(?:,|before\b|on\b|who\b|which\b|has\b|is\b|$))",
    flags=re.I | re.S,
)
NAME_FIELD_PAT = re.compile(
    r"\bName\.?\s*([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4})"
    r"(?=\s*(?:\.|Age\b|Caste\b|Makment\b|Statement\b|$))",
    flags=re.I,
)
ORIGINAL_NAME_CASE_PAT = re.compile(
    r"\b(?:boy|girl|woman|youth)\s+([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,3})"
    r"\s*,\s*original\s+name\b",
    flags=re.I,
)
NAMED_CASE_PAT = re.compile(
    r"\b(?:named|called)\s+"
    r"([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4}(?:\s+Baluchi)?)\b",
    flags=re.I,
)
POSSESSIVE_RELATION_SEED_PAT = re.compile(
    r"\b([A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4}(?:'s|s')\s+"
    r"(?:sister|wife|son|daughter|child|children))\b",
    flags=re.I,
)
OF_RELATION_SEED_PAT = re.compile(
    r"\b((?:sister|wife|son|daughter|child|children)\s+of\s+"
    r"[A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4})\b",
    flags=re.I,
)
FEMALE_SLAVE_OF_SEED_PAT = re.compile(
    r"\b(female\s+slave\s+of\s+[A-Z][A-Za-z']+(?:\s+(?:bin|bint|al|el|ibn|Abu|Umm)\s+[A-Z][A-Za-z']+){0,4})\b",
    flags=re.I,
)
SEGMENT_SUBJECT_SIGNAL_PAT = re.compile(
    r"\b("
    r"kidnapped|captured|stolen|sold|resold|recovered|repatriated|"
    r"took\s+bast|took\s+refuge|come\s+here\s+to\s+complain|has\s+come\s+here\s+to\s+complain|"
    r"kept\s+as\s+a\s+slave|as\s+a\s+slave|originally\s+.*?slave|actually\s+.*?slave|"
    r"not\s+his\s+slave|slave\s+of|master|manumission\s+certificate|freedom|free\s+him|released"
    r")\b",
    flags=re.I | re.S,
)
RELATION_SUBJECT_SIGNAL_PAT = re.compile(
    r"\b(?:slave|kept\s+as\s+a\s+slave|stolen|kidnapped|captured|sold|sale|freedom|manumission|recover(?:ed)?|"
    r"release(?:d)?|sent\s+away|certificate|took\s+bast|took\s+refuge|carried\s+off)\b",
    flags=re.I,
)
RELATION_DIRECT_SUBJECT_PAT = re.compile(
    r"^\W*(?:who\s+)?(?:was|is|has\s+been|had\s+been|being)?\W*.{0,80}\b"
    r"(?:kidnapped|captured|stolen|sold|kept\s+as\s+a\s+slave|recovered|released|sent\s+away|"
    r"took\s+bast|took\s+refuge|manumission|freedom|certificate|carried\s+off)\b",
    flags=re.I | re.S,
)
RELATION_NEGATIVE_CONTEXT_PAT = re.compile(
    r"\b(?:witness(?:es)?|proof|people\s+who\s+know|persons?\s+who\s+know|owner|master|broker|seller|kidnapper)\b",
    flags=re.I,
)
FEMALE_RELATIVE_SIGNAL_PAT = re.compile(
    r"\b(?:she|her|woman|sister|lady)\b.{0,220}\b(?:kidnapped|sold|sale|slave|freedom|manumission|examin(?:e|ation)|sent\s+away)\b",
    flags=re.I | re.S,
)
MEMORANDUM_SUBJECT_SIGNAL_PAT = re.compile(
    r"\b(?:aged\s+\d+|boy|boys|woman|women|youth|Baluch(?:i|is)?|Hindu)\b.{0,260}\b"
    r"(?:kidnapped|captured|sold|recovered|repatriated|took\s+bast|manumission\s+certificate|traced|found)\b",
    flags=re.I | re.S,
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


def _iter_name_matches(name: str, ocr: str) -> list[re.Match[str]]:
    pattern = build_name_regex(name)
    if not pattern or not ocr:
        return []
    return list(pattern.finditer(ocr))


def iter_name_contexts(name: str, ocr: str, window: int = 320) -> list[str]:
    if not ocr:
        return []
    contexts: list[str] = []
    for match in _iter_name_matches(name, ocr):
        start = max(0, match.start() - window)
        end = min(len(ocr), match.end() + window)
        contexts.append(normalize_ws(ocr[start:end]))
    return contexts


def iter_name_segments(name: str, ocr: str) -> list[str]:
    if not ocr:
        return []
    segments: list[str] = []
    for match in _iter_name_matches(name, ocr):
        start = _segment_start(ocr, match.start())
        end = _segment_end(ocr, match.end())
        segment = normalize_ws(ocr[start:end])
        if segment and segment not in segments:
            segments.append(segment)
    return segments


def name_has_ocr_context(name: str, ocr: str) -> bool:
    return bool(iter_name_contexts(name, ocr))


def _segment_start(text: str, pos: int) -> int:
    markers = [
        text.rfind("\n\n", 0, pos),
        max((m.start() for m in re.finditer(r"(?:^|\n)\s*\(\d+\)", text[:pos])), default=-1),
        max((m.start() for m in re.finditer(r"(?:^|\n)\s*\d+[\).]\s+", text[:pos])), default=-1),
    ]
    marker = max(markers)
    if marker >= 0:
        return marker
    return max(0, pos - 900)


def _segment_end(text: str, pos: int) -> int:
    candidates = [len(text)]
    for pattern in (r"\n\s*\n", r"(?:^|\n)\s*\(\d+\)", r"(?:^|\n)\s*\d+[\).]\s+"):
        match = re.search(pattern, text[pos:])
        if match:
            candidates.append(pos + match.start())
    return min(candidates + [min(len(text), pos + 1200)])


def compile_name_phrase(pattern_template: str, name: str) -> re.Pattern[str]:
    tokens = [re.escape(token).replace("'", "['’]") for token in normalize_name(name).split() if token]
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


def segment_positive_matches(name: str, segments: list[str], ocr: str = "") -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    candidate_segments = list(segments)
    is_relation_label = looks_like_subject_label(name)
    first_token = "" if is_relation_label else _first_name_token(name)
    if first_token and first_token != normalize_for_match(name):
        for segment in iter_name_segments(first_token, ocr):
            if segment not in candidate_segments:
                candidate_segments.append(segment)
    for segment in candidate_segments:
        if not segment or not name_has_ocr_context(name, segment):
            if not first_token or not name_has_ocr_context(first_token, segment):
                continue
            active_name = first_token
        else:
            active_name = name
        name_pat = compile_name_phrase(r"\b{name}\b", active_name)
        name_match = name_pat.search(segment)
        if not name_match:
            continue
        local = segment[max(0, name_match.start() - 80) : min(len(segment), name_match.end() + 360)]
        if is_relation_label:
            relation_window = segment[max(0, name_match.start() - 20) : min(len(segment), name_match.end() + 180)]
            relation_after = segment[name_match.end() : min(len(segment), name_match.end() + 180)]
            negative_window = segment[max(0, name_match.start() - 120) : min(len(segment), name_match.end() + 40)]
            direct_subject = RELATION_DIRECT_SUBJECT_PAT.search(relation_after) or normalize_for_match(name).startswith("female slave of ")
            if direct_subject and RELATION_SUBJECT_SIGNAL_PAT.search(relation_window) and not RELATION_NEGATIVE_CONTEXT_PAT.search(negative_window):
                hits.append(
                    {
                        "key": "relation_label_subject_context",
                        "reason_type": "positive_rule",
                        "reason": "matched relation-labeled subject with slavery/manumission context",
                        "excerpt": clean_evidence(relation_window),
                    }
                )
            continue
        checks = [
            (
                "name_field_subject",
                compile_name_phrase(r"\bname\.?\s*{name}\b", name).search(segment)
                and SEGMENT_SUBJECT_SIGNAL_PAT.search(segment),
                'matched "Name. {name}" with subject context',
            ),
            (
                "female_relative_sale_context",
                (
                    compile_name_phrase(r"\b(?:his|her|the)\s+sister\s+{name}\b", name).search(segment)
                    or compile_name_phrase(r"\bwoman\s+(?:named\s+)?{name}\b", name).search(segment)
                    or compile_name_phrase(r"\bthe\s+woman\s+{name}\b", name).search(segment)
                )
                and FEMALE_RELATIVE_SIGNAL_PAT.search(segment),
                "matched named woman/sister with sale, slavery, or freedom context",
            ),
            (
                "named_subject_victim_context",
                compile_name_phrase(r"\b(?:named|called|by\s+(?:the\s+)?name\s+of)\s+{name}\b", name).search(segment)
                and SEGMENT_SUBJECT_SIGNAL_PAT.search(segment),
                'matched "named {name}" with subject context',
            ),
            (
                "complaint_subject_context",
                compile_name_phrase(r"\b{name}\b", name).search(segment)
                and re.search(r"\b(?:negro|boy|man)\b.{0,120}\b(?:come|has\s+come)\s+here\s+to\s+complain\b", segment, flags=re.I | re.S)
                and SEGMENT_SUBJECT_SIGNAL_PAT.search(segment),
                "matched named complainant subject context",
            ),
            (
                "slave_status_investigation",
                compile_name_phrase(r"\b{name}\b", active_name).search(segment)
                and re.search(r"\b(?:not\s+his\s+slave|actually\s+.*?slave|originally\s+.*?slave|barwah|transferr?ed|ran\s+away|absconded|master\s+salim|rs\.?\s*\d+)\b", segment, flags=re.I | re.S),
                "matched slave-status investigation context",
            ),
            (
                "relation_recovered_context",
                compile_name_phrase(r"\b{name}\b", active_name).search(segment)
                and re.search(r"\b(?:son|sons|daughter|relation|child|children)\b", local, flags=re.I)
                and re.search(r"\b(?:traced|found|recovered|released|freedom|manumission)\b", local, flags=re.I),
                "matched named relation with recovery/freedom context",
            ),
            (
                "numbered_case_victim",
                MEMORANDUM_SUBJECT_SIGNAL_PAT.search(local) or (
                    re.search(r"\(\d+\)|(?:^|\s)\d+[\).]", segment)
                    and SEGMENT_SUBJECT_SIGNAL_PAT.search(local)
                ),
                "matched numbered or memorandum victim case context",
            ),
        ]
        for key, matched, reason in checks:
            if matched:
                hits.append(
                    {
                        "key": key,
                        "reason_type": "positive_rule",
                        "reason": reason.replace("{name}", canonicalize_candidate_name(name)),
                        "excerpt": clean_evidence(local),
                    }
                )
                break
    return hits


def rule_seed_candidates(ocr: str) -> list[dict[str, str]]:
    seeds: list[dict[str, str]] = []
    text = ocr or ""
    def add_seed(name: str, evidence: str) -> None:
        raw = str(name or "").strip()
        if raw and not raw[0].isupper() and not looks_like_subject_label(raw):
            return
        cleaned = canonicalize_candidate_name(name)
        if cleaned and looks_like_candidate_name(cleaned):
            seeds.append({"name": cleaned, "evidence": clean_evidence(evidence)})

    for match in CERTAIN_NEGRO_PAT.finditer(text):
        add_seed(match.group(1), match.group(0))
    for pattern in (SUBJECT_NAMED_PAT, REVERSE_BY_NAME_PAT, STATEMENT_OF_NAME_PAT, NAME_FIELD_PAT, ORIGINAL_NAME_CASE_PAT, NAMED_CASE_PAT):
        for match in pattern.finditer(text):
            local = text[max(0, match.start() - 80) : min(len(text), match.end() + 120)]
            if re.search(r"\bslave\s+of\s+(?:a\s+)?man\s+named\b", local, flags=re.I):
                continue
            if re.search(r"\bma?khudha\s+named\b", local, flags=re.I):
                continue
            add_seed(match.group(1), match.group(0))
    for match in POSSESSIVE_RELATION_SEED_PAT.finditer(text.replace("’", "'")):
        add_seed(match.group(1), match.group(0))
    for match in OF_RELATION_SEED_PAT.finditer(text.replace("’", "'")):
        prefix = text[max(0, match.start() - 16) : match.start()]
        prefix_words = re.findall(r"[A-Za-z']+", prefix)
        if prefix_words and prefix_words[-1].lower() not in {"a", "an", "the"} and re.search(r"[A-Za-z']\s+$", prefix):
            continue
        add_seed(match.group(1), match.group(0))
    for match in FEMALE_SLAVE_OF_SEED_PAT.finditer(text.replace("’", "'")):
        local = text[max(0, match.start() - 120) : min(len(text), match.end() + 220)]
        if RELATION_SUBJECT_SIGNAL_PAT.search(local):
            add_seed(match.group(1), match.group(0))
    for match in PLURAL_SUBJECT_LIST_PAT.finditer(text):
        intro = clean_evidence(match.group(0))
        for name_match in NUMBERED_NAME_PAT.finditer(match.group("body") or ""):
            add_seed(name_match.group(1), intro)
    return merge_name_candidates(seeds)


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
    if compile_name_phrase(TITLE_BEFORE_NAME_TEMPLATE, name).search(text):
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
    cleaned_name = canonicalize_candidate_name(name)
    cleaned_evidence = clean_evidence(evidence)
    if is_generic_subject_phrase(cleaned_name):
        return {
            "keep": False,
            "reason_type": "generic_subject_phrase",
            "reason": "Candidate is an unnamed generic subject phrase, not a real personal name.",
            "excerpt": cleaned_evidence,
            "positive_matches": [],
            "negative_matches": [],
        }
    contexts = iter_name_contexts(cleaned_name, ocr)
    if not contexts:
        return {
            "keep": False,
            "reason_type": "name_absent_from_ocr",
            "reason": "Candidate name is not present on this OCR page.",
            "excerpt": cleaned_evidence or clean_evidence(ocr),
            "positive_matches": [],
            "negative_matches": [],
        }

    segments = iter_name_segments(cleaned_name, ocr)
    texts = [text for text in [*contexts, *segments] if text]
    positive = [hit for text in texts for hit in positive_matches(cleaned_name, text)]
    positive.extend(segment_positive_matches(cleaned_name, segments, ocr))
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

    hard_negative = next((hit for hit in negative if hit.get("key") in HARD_NEGATIVE_KEYS), None)
    if hard_negative:
        return {
            "keep": False,
            "reason_type": hard_negative["reason_type"],
            "reason": hard_negative["reason"],
            "excerpt": hard_negative["excerpt"],
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
        normalized = {"name": canonicalize_candidate_name(item.get("name", "")), "evidence": clean_evidence(item.get("evidence", ""))}
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
