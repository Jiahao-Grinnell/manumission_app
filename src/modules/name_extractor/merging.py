from __future__ import annotations

import re

from modules.normalizer.names import (
    choose_preferred_name as choose_normalizer_preferred_name,
    is_valid_name,
    names_maybe_same_person,
    normalize_name,
)
from modules.normalizer.vocabulary import NAME_STOPWORDS


GENERIC_NAME_WORDS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "slave",
    "slaves",
    "negro",
    "negroes",
    "man",
    "woman",
    "boy",
    "girl",
    "person",
    "people",
    "female",
    "females",
    "male",
    "males",
    "women",
    "men",
    "children",
    "number",
    "group",
    "lot",
    "some",
    "several",
    "many",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "with",
    "of",
    "in",
    "question",
    "subject",
    "applicant",
}

RELATION_WORDS = {"sister", "wife", "son", "daughter", "child", "children"}
ANCHOR_STOPWORDS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "his",
    "her",
    "their",
    "our",
    "my",
    "your",
    "its",
    "one",
    "two",
    "three",
}

GENERIC_SUBJECT_PATTERN = re.compile(
    r"^(?:a|an|the|this|that)?\s*"
    r"(?:(?:number|group|lot|some|several|many|two|three|four|five|six|seven|eight|nine|ten)\s+)?"
    r"(?:of\s+)?"
    r"(?:slave|slaves|negro|negroes|female|females|male|males|woman|women|man|men|boy|boys|girl|girls|child|children)"
    r"(?:\s+with\s+(?:two|three|four|five|six|seven|eight|nine|ten|\d+)?\s*(?:child|children|boys|girls))?"
    r"$",
    flags=re.I,
)
POSSESSIVE_RELATION_PATTERN = re.compile(
    r"^(?P<anchor>[A-Za-z][A-Za-z' -]*?)(?:'s|s')\s+(?P<relation>sister|wife|son|daughter|child|children)$",
    flags=re.I,
)
OF_RELATION_PATTERN = re.compile(
    r"^(?P<relation>sister|wife|son|daughter|child|children)\s+of\s+(?P<anchor>[A-Za-z][A-Za-z' -]*?[A-Za-z])$",
    flags=re.I,
)
FEMALE_SLAVE_OF_PATTERN = re.compile(r"^female\s+slave\s+of\s+(?P<anchor>[A-Za-z][A-Za-z' -]*?[A-Za-z])$", flags=re.I)

ROLE_DESCRIPTOR_PATTERNS = [
    re.compile(r"\b(?:slave|negro)\s+of\b.*$", flags=re.I),
    re.compile(r"\b(?:original\s+name|aged|age|caste)\b.*$", flags=re.I),
]
DANGLING_KINSHIP_PATTERN = re.compile(r"\b(?:son|daughter)\s+of\s*$", flags=re.I)


def canonicalize_candidate_name(name: str) -> str:
    """Trim role/descriptor text that the model sometimes folds into a name."""
    value = normalize_name(str(name or "").replace("’", "'"))
    if not value:
        return ""
    if looks_like_subject_label(value):
        return normalize_subject_label(value)
    value = re.sub(r"[,;:]+", " ", value)
    for pattern in ROLE_DESCRIPTOR_PATTERNS:
        value = pattern.sub("", value)
    value = DANGLING_KINSHIP_PATTERN.sub("", value)
    return normalize_name(value)


def _anchor_has_real_name(anchor: str) -> bool:
    normalized = normalize_name(anchor)
    words = [word.lower().strip("'") for word in normalized.split()]
    if not words or set(words) & ANCHOR_STOPWORDS:
        return False
    if len(words) == 1 and words[0] in GENERIC_NAME_WORDS:
        return False
    return is_valid_name(normalized)


def looks_like_subject_label(name: str) -> bool:
    normalized = normalize_name(str(name or "").replace("’", "'"))
    if not normalized or re.search(r"\d", normalized):
        return False
    for pattern in (POSSESSIVE_RELATION_PATTERN, OF_RELATION_PATTERN, FEMALE_SLAVE_OF_PATTERN):
        match = pattern.match(normalized)
        if match and _anchor_has_real_name(match.group("anchor")):
            return True
    return False


def normalize_subject_label(name: str) -> str:
    normalized = normalize_name(str(name or "").replace("’", "'"))
    match = POSSESSIVE_RELATION_PATTERN.match(normalized)
    if match and _anchor_has_real_name(match.group("anchor")):
        return f"{normalize_name(match.group('anchor'))}'s {match.group('relation').lower()}"
    match = OF_RELATION_PATTERN.match(normalized)
    if match and _anchor_has_real_name(match.group("anchor")):
        return f"{match.group('relation').lower()} of {normalize_name(match.group('anchor'))}"
    match = FEMALE_SLAVE_OF_PATTERN.match(normalized)
    if match and _anchor_has_real_name(match.group("anchor")):
        return f"female slave of {normalize_name(match.group('anchor'))}"
    return normalized


def is_generic_subject_phrase(name: str) -> bool:
    normalized = canonicalize_candidate_name(name)
    words = [word.lower() for word in normalized.split()]
    if not words:
        return True
    if looks_like_subject_label(normalized):
        return False
    if GENERIC_SUBJECT_PATTERN.match(normalized):
        return True
    if len(words) <= 3 and all(word in GENERIC_NAME_WORDS for word in words):
        return True
    return len(words) <= 2 and bool(set(words) & {"slave", "slaves", "negro", "negroes"}) and not any(
        word not in GENERIC_NAME_WORDS for word in words
    )


def looks_like_candidate_name(name: str) -> bool:
    normalized = canonicalize_candidate_name(name)
    if is_generic_subject_phrase(normalized):
        return False
    if looks_like_subject_label(normalized):
        return True
    if is_valid_name(normalized):
        return True
    if not normalized or re.search(r"\d", normalized):
        return False
    words = [word.lower() for word in normalized.split()]
    if set(words) & NAME_STOPWORDS:
        return False
    return len(words) >= 2 and sum(ch.isalpha() for ch in normalized) >= 4


def merge_name_candidates(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for group in groups:
        for item in group or []:
            name = canonicalize_candidate_name(str(item.get("name") or ""))
            if looks_like_candidate_name(name):
                items.append({"name": name, "evidence": str(item.get("evidence") or "")})

    clusters: list[list[dict[str, str]]] = []
    for item in items:
        for cluster in clusters:
            if any(_candidates_maybe_same_subject(item["name"], other["name"]) for other in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    merged = [choose_preferred_name(cluster) for cluster in clusters if cluster]
    return sorted(merged, key=lambda item: item["name"].lower())


def choose_preferred_name(items: list[dict[str, str]]) -> dict[str, str]:
    preferred = choose_normalizer_preferred_name(items)
    return {"name": canonicalize_candidate_name(preferred.get("name") or ""), "evidence": preferred.get("evidence", "")}


def _candidates_maybe_same_subject(a: str, b: str) -> bool:
    a_label = looks_like_subject_label(a)
    b_label = looks_like_subject_label(b)
    if a_label or b_label:
        return a_label and b_label and normalize_name(a).casefold() == normalize_name(b).casefold()
    return names_maybe_same_person(a, b)


__all__ = [
    "canonicalize_candidate_name",
    "choose_preferred_name",
    "is_generic_subject_phrase",
    "looks_like_subject_label",
    "looks_like_candidate_name",
    "merge_name_candidates",
    "names_maybe_same_person",
    "normalize_subject_label",
]
