from __future__ import annotations

import difflib
import re
from typing import Any

from shared.text_utils import normalize_ws, strip_accents

from .vocabulary import NAME_STOPWORDS


CONNECTORS = {"bin", "bint", "al", "el", "ul", "ibn"}
COMPARE_SKIP_TOKENS = {"bin", "bint", "ibn", "son", "daughter", "of", "al", "el", "ul"}


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = strip_accents(normalize_ws(str(name)))
    s = s.strip(" ,.;:[]{}\"'")
    s = re.sub(r"^(?:the\s+)?slave\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:mr|mrs|miss|mst)\.?\s+", "", s, flags=re.I)
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = normalize_ws(s)
    tokens: list[str] = []
    for token in s.split():
        low = token.lower()
        if low in CONNECTORS:
            tokens.append("bin" if low == "ibn" else low)
        elif low in {"abu", "umm"}:
            tokens.append(low.title())
        elif low in {"daughter", "son", "of"}:
            tokens.append(low)
        else:
            tokens.append(token[:1].upper() + token[1:].lower())
    return normalize_ws(" ".join(tokens))


def is_valid_name(name: str) -> bool:
    if not name:
        return False
    s = normalize_name(name)
    if len(s) < 2 or re.search(r"\d", s):
        return False
    words = s.split()
    if not words:
        return False
    low_words = {word.lower() for word in words}
    if low_words & NAME_STOPWORDS and len(words) <= 2:
        return False
    return sum(ch.isalpha() for ch in s) >= 2


def name_compare_tokens(name: str) -> list[str]:
    return [
        token.lower()
        for token in normalize_name(name).split()
        if token.lower() not in COMPARE_SKIP_TOKENS
    ]


def explain_name_comparison(a: str, b: str) -> dict[str, Any]:
    na = normalize_name(a)
    nb = normalize_name(b)
    ta = name_compare_tokens(na)
    tb = name_compare_tokens(nb)
    if not na or not nb or not ta or not tb:
        return {"same": False, "reason": "missing comparable tokens", "tokens_a": ta, "tokens_b": tb, "overlap": 0.0}
    if na.lower() == nb.lower():
        return {"same": True, "reason": "exact normalized match", "tokens_a": ta, "tokens_b": tb, "overlap": 1.0}
    seq_ratio = difflib.SequenceMatcher(None, na.lower(), nb.lower()).ratio()
    flat_ratio = difflib.SequenceMatcher(None, "".join(ta), "".join(tb)).ratio()
    overlap = len(set(ta) & set(tb)) / max(len(set(ta)), len(set(tb)), 1)
    same = False
    reason = f"sequence {seq_ratio:.2f}, flat {flat_ratio:.2f}, token overlap {overlap:.2f}"
    if ta[0] != tb[0]:
        first_ratio = difflib.SequenceMatcher(None, ta[0], tb[0]).ratio()
        same = flat_ratio >= 0.82 and first_ratio >= 0.83
        reason = f"first tokens differ; first-token ratio {first_ratio:.2f}, flat ratio {flat_ratio:.2f}"
    elif ta == tb:
        same = True
        reason = "same comparison tokens"
    elif na.lower() in nb.lower() or nb.lower() in na.lower():
        same = True
        reason = "one normalized name contains the other"
    elif seq_ratio >= 0.9 or flat_ratio >= 0.8 or overlap >= 0.75:
        same = True
    return {
        "same": same,
        "reason": reason,
        "normalized_a": na,
        "normalized_b": nb,
        "tokens_a": ta,
        "tokens_b": tb,
        "overlap": round(overlap, 3),
        "sequence_ratio": round(seq_ratio, 3),
        "flat_ratio": round(flat_ratio, 3),
    }


def names_maybe_same_person(a: str, b: str) -> bool:
    return bool(explain_name_comparison(a, b)["same"])


def choose_preferred_name(items: list[dict[str, str]]) -> dict[str, str]:
    def score(item: dict[str, str]) -> tuple[int, int, int, str]:
        name = normalize_name(item.get("name") or "")
        tokens = name_compare_tokens(name)
        return (len(tokens), len(name), len(item.get("evidence") or ""), name.lower())

    preferred = max(items, key=score)
    return {"name": normalize_name(preferred.get("name") or ""), "evidence": preferred.get("evidence", "")}


def merge_named_people(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for group in groups:
        for item in group or []:
            name = normalize_name(str(item.get("name") or ""))
            if is_valid_name(name):
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


def build_name_regex(name: str) -> re.Pattern[str] | None:
    tokens = [re.escape(token) for token in normalize_name(name).split() if token]
    if not tokens:
        return None
    joined = r"[\s,.;:'\"()\-]+".join(tokens)
    return re.compile(r"\b" + joined + r"\b", flags=re.I)
