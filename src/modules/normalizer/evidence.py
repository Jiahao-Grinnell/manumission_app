from __future__ import annotations

import re
from typing import Any

from shared.text_utils import normalize_ws, strip_accents


def clean_evidence(text: Any, *, max_words: int = 25) -> str:
    s = normalize_ws(str(text or ""))
    if not s:
        return ""
    return " ".join(s.split()[:max_words])


def normalize_for_match(text: str) -> str:
    s = strip_accents(normalize_ws(text)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return normalize_ws(s)
