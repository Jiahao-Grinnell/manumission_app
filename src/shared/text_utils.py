from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def strip_accents(s: str) -> str:
    normalized = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def clean_ocr(s: str) -> str:
    text = (s or "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        lines.append(re.sub(r"[\t ]+", " ", line).strip())
    return "\n".join(lines).strip()


def extract_json(s: str) -> Any | None:
    text = s or ""

    try:
        return json.loads(text.strip())
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        parsed = extract_json(fenced.group(1))
        if parsed is not None:
            return parsed

    start = _first_json_start(text)
    if start is None:
        return None
    end = _matching_json_end(text, start)
    if end is None:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def render_prompt(template: str, **kwargs: Any) -> str:
    return template.format(**kwargs)


def _first_json_start(text: str) -> int | None:
    positions = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not positions:
        return None
    return min(positions)


def _matching_json_end(text: str, start: int) -> int | None:
    opener = text[start]
    if opener not in "{[":
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return index

    return None
