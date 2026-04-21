from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shared.config import settings


DEFAULT_DETAIL_REPORT_TYPES = ["statement", "correspondence"]
DEFAULT_CRIME_TYPES = ["kidnapping", "illegal detention"]
DEFAULT_WHETHER_ABUSE_VALUES = ["yes", "no"]
DEFAULT_CONFLICT_TYPES = [
    "manumission dispute",
    "ownership dispute",
    "debt dispute",
    "free-status dispute",
    "forced-transfer dispute",
    "repatriation dispute",
    "kidnapping case",
]
DEFAULT_TRIAL_TYPES = [
    "manumission requested",
    "freedom/manumission outcome",
    "repatriation arranged",
]


def _vocab_path() -> Path:
    return settings.PROMPT_DIR.parent / "schemas" / "vocab.yaml"


def _load_yaml() -> dict[str, Any]:
    path = _vocab_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _ordered_list(data: dict[str, Any], key: str, default: list[str]) -> list[str]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return list(default)
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, bool):
            value = "yes" if item else "no"
        else:
            value = str(item).strip()
        if not value:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        values.append(value)
    return values or list(default)


_VOCAB = _load_yaml()

DETAIL_REPORT_TYPES = _ordered_list(_VOCAB, "detail_report_types", DEFAULT_DETAIL_REPORT_TYPES)
CRIME_TYPES = _ordered_list(_VOCAB, "crime_types", DEFAULT_CRIME_TYPES)
WHETHER_ABUSE_VALUES = _ordered_list(_VOCAB, "whether_abuse_values", DEFAULT_WHETHER_ABUSE_VALUES)
CONFLICT_TYPES = _ordered_list(_VOCAB, "conflict_types", DEFAULT_CONFLICT_TYPES)
TRIAL_TYPES = _ordered_list(_VOCAB, "trial_types", DEFAULT_TRIAL_TYPES)
