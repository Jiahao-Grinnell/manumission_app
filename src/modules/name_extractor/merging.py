from __future__ import annotations

import re

from modules.normalizer.names import choose_preferred_name, is_valid_name, names_maybe_same_person, normalize_name


def looks_like_candidate_name(name: str) -> bool:
    normalized = normalize_name(name)
    if is_valid_name(normalized):
        return True
    if not normalized or re.search(r"\d", normalized):
        return False
    return len(normalized.split()) >= 2 and sum(ch.isalpha() for ch in normalized) >= 4


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


__all__ = ["choose_preferred_name", "looks_like_candidate_name", "merge_name_candidates", "names_maybe_same_person"]
