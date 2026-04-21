from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .config import settings


def load_prompt_text(
    *relative_parts: str,
    prompt_dir: Path | None = None,
    legacy_names: Iterable[str] | None = None,
    fallback_text: str = "",
) -> str:
    base_dir = prompt_dir or settings.PROMPT_DIR
    candidates: list[Path] = []
    if relative_parts:
        candidates.append(base_dir.joinpath(*relative_parts))
    for legacy_name in legacy_names or ():
        candidates.append(base_dir / legacy_name)

    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return fallback_text.strip()
