from __future__ import annotations

import re

from modules.normalizer.names import choose_preferred_name, is_valid_name, names_maybe_same_person, normalize_name
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
    "subject",
    "applicant",
}


def is_generic_subject_phrase(name: str) -> bool:
    normalized = normalize_name(name)
    words = [word.lower() for word in normalized.split()]
    if not words:
        return True
    if len(words) <= 3 and all(word in GENERIC_NAME_WORDS for word in words):
        return True
    return len(words) <= 2 and bool(set(words) & {"slave", "slaves", "negro", "negroes"}) and not any(
        word not in GENERIC_NAME_WORDS for word in words
    )


def looks_like_candidate_name(name: str) -> bool:
    normalized = normalize_name(name)
    if is_generic_subject_phrase(normalized):
        return False
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
            name = normalize_name(str(item.get("name") or ""))
            if looks_like_candidate_name(name):
                items.append({"name": name, "evidence": str(item.get("evidence") or "")})

    clusters: list[list[dict[str, str]]] = []
    for item in items:
        for cluster in clusters:
            if any(names_maybe_same_person(item["name"], other["name"]) for other in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    merged = [choose_preferred_name(cluster) for cluster in clusters if cluster]
    return sorted(merged, key=lambda item: item["name"].lower())


__all__ = [
    "choose_preferred_name",
    "is_generic_subject_phrase",
    "looks_like_candidate_name",
    "merge_name_candidates",
    "names_maybe_same_person",
]
