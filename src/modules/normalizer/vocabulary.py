from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shared.config import settings


DEFAULT_NAME_STOPWORDS = {
    "slave",
    "slaves",
    "woman",
    "man",
    "boy",
    "girl",
    "unknown",
    "unnamed",
    "agency",
    "resident",
    "secretary",
    "captain",
    "major",
    "sheikh",
    "shaikh",
    "political",
    "residency",
    "certificate",
    "statement",
    "memorandum",
    "telegram",
    "master",
    "owner",
    "buyer",
    "seller",
    "agent",
    "office",
}

DEFAULT_PLACE_STOPWORDS = {
    "unknown",
    "unclear",
    "none",
    "nil",
    "there",
    "here",
    "this agency",
    "the agency",
    "agency",
    "residency",
    "political agency",
    "residency agency",
    "office",
    "record",
    "statement",
    "memorandum",
    "certificate",
    "arrival",
}

DEFAULT_PLACE_MAP = {
    "shargah": "Sharjah",
    "sharjeh": "Sharjah",
    "sharjah": "Sharjah",
    "dibai": "Dubai",
    "debai": "Dubai",
    "dubai": "Dubai",
    "bahrein": "Bahrain",
    "bahrain": "Bahrain",
    "bushire": "Bushehr",
    "busheir": "Bushehr",
    "bushehr": "Bushehr",
    "mekran": "Mekran",
    "mokran": "Mekran",
    "henjam": "Henjam",
    "honjam": "Henjam",
    "ras ul khaimah": "Ras al Khaimah",
    "ras al khaimah": "Ras al Khaimah",
    "umm al quwain": "Umm al Quwain",
    "umm ul quwain": "Umm al Quwain",
    "muscat": "Muscat",
    "mascat": "Muscat",
    "zanzibar": "Zanzibar",
    "abyssinia": "Abyssinia",
    "abisinia": "Abyssinia",
}


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


def _list_set(data: dict[str, Any], key: str, default: set[str]) -> set[str]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return set(default)
    values = {str(item).strip().lower() for item in raw if str(item).strip()}
    return set(default) | values


def _place_map(data: dict[str, Any]) -> dict[str, str]:
    mapping = dict(DEFAULT_PLACE_MAP)
    raw = data.get("place_map")
    if isinstance(raw, dict):
        for key, value in raw.items():
            src = str(key).strip().lower()
            dst = str(value).strip()
            if src and dst:
                mapping[src] = dst
    return mapping


_VOCAB = _load_yaml()
NAME_STOPWORDS = _list_set(_VOCAB, "name_stopwords", DEFAULT_NAME_STOPWORDS)
PLACE_STOPWORDS = _list_set(_VOCAB, "place_stopwords", DEFAULT_PLACE_STOPWORDS)
PLACE_MAP = _place_map(_VOCAB)
