from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def write_json_atomic(path: Path, obj: Any) -> None:
    _atomic_text_write(path, json.dumps(obj, indent=2, ensure_ascii=False))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv_atomic(path: Path, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="",
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def artifact_ok(path: Path, kind: str) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if kind in {"text", "ocr_text"}:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return bool(text) or text == "[OCR_EMPTY]"
    if kind == "json":
        try:
            read_json(path)
        except Exception:
            return False
        return True
    return path.stat().st_size > 0


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
