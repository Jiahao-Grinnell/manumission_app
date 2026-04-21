from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import requests

from shared.config import settings
from shared.prompt_loader import load_prompt_text
from shared.storage import write_json_atomic

from .preprocessing import b64_png, preprocess_page


DEFAULT_OCR_PROMPT = (
    "You are an OCR engine. Transcribe ALL visible text from the image.\n"
    "Rules:\n"
    "- Output ONLY the text (no markdown, no code fences).\n"
    "- Preserve line breaks as best as possible.\n"
    "- Do not add commentary or explanations.\n"
    "- If you cannot read any text, output exactly: [OCR_EMPTY]\n"
)

ProgressCallback = Callable[[str, int, int, Path], None]
_FENCE_LINE = re.compile(r"^\s*```(?:[a-zA-Z0-9_-]+)?\s*$")


@dataclass(frozen=True)
class OcrResult:
    image_path: Path
    out_file: Path
    text: str
    status: str
    elapsed_seconds: float
    model: str
    tile_count: int
    debug_files: list[str] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_prompt(prompt: str | None = None) -> str:
    if prompt:
        return prompt
    return load_prompt_text(
        "ocr",
        "ocr.txt",
        legacy_names=["ocr.txt"],
        fallback_text=DEFAULT_OCR_PROMPT,
    )


def cleanup_ocr_text(text: str) -> str:
    if not text:
        return ""
    lines = []
    for line in str(text).splitlines():
        if _FENCE_LINE.match(line):
            continue
        stripped = line.rstrip()
        if re.match(r"^\s*(?:here is|the text in the image|transcription)\b", stripped, flags=re.I):
            continue
        lines.append(stripped)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_effectively_empty(text: str) -> bool:
    cleaned = cleanup_ocr_text(text or "").strip()
    return cleaned != "[OCR_EMPTY]" and len(cleaned) < 5


def should_skip_existing(out_file: Path) -> bool:
    try:
        if not out_file.exists() or out_file.stat().st_size == 0:
            return False
        head = out_file.read_text(encoding="utf-8", errors="replace")[:2000]
        return head.strip() == "[OCR_EMPTY]" or not is_effectively_empty(head)
    except Exception:
        return False


def wait_for_ollama_ready(ollama_generate_url: str, timeout_s: int = 180) -> None:
    base = ollama_generate_url.split("/api/")[0].rstrip("/")
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            response = requests.get(f"{base}/api/version", timeout=5)
            if response.status_code == 200:
                return
            last_error = response.text[:500]
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise TimeoutError(f"Ollama was not ready after {timeout_s}s: {last_error}")


def extract_text_from_ollama_json(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, str) and error.strip():
        raise RuntimeError(error.strip())
    if isinstance(data.get("response"), str):
        return data["response"]
    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    for key in ("output", "text", "content"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def ollama_ocr_one_image(
    *,
    ollama_generate_url: str,
    model: str,
    image_b64: str,
    prompt: str,
    timeout_s: int,
    num_predict: int,
    debug_json_path: Path | None = None,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": num_predict},
    }
    response = requests.post(ollama_generate_url, json=payload, timeout=timeout_s)
    if response.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {response.status_code} from {ollama_generate_url}. Body: {(response.text or '')[:2000]}")
    try:
        data = response.json()
    except Exception:
        if debug_json_path:
            debug_json_path.write_text(response.text or "", encoding="utf-8")
        return ""
    if debug_json_path:
        debug_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return cleanup_ocr_text(extract_text_from_ollama_json(data))


def ocr_page(
    image_path: str | Path,
    out_file: str | Path | None = None,
    *,
    model: str | None = None,
    ollama_generate_url: str | None = None,
    prompt: str | None = None,
    preprocess_long: int = 2600,
    min_long_for_ocr: int = 1800,
    tile: bool = True,
    max_new_tokens: int = 1200,
    timeout_s: int = 240,
    debug_dir: str | Path | None = None,
) -> OcrResult:
    image = Path(image_path)
    output = Path(out_file) if out_file else image.with_suffix(".txt")
    selected_model = model or settings.OCR_MODEL
    selected_url = ollama_generate_url or settings.OLLAMA_URL
    selected_prompt = load_prompt(prompt)
    page_start = time.time()

    img = cv2.imread(str(image))
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {image}")

    debug_root = Path(debug_dir) if debug_dir else None
    if debug_root:
        debug_root.mkdir(parents=True, exist_ok=True)

    prep = preprocess_page(img, preprocess_long=preprocess_long, min_long_for_ocr=min_long_for_ocr, tile=tile)
    debug_files: list[str] = []
    texts: list[str] = []

    for index, tile_img in enumerate(prep.tiles_bgr):
        if debug_root:
            prep_path = debug_root / f"{image.stem}__prep_{index}.png"
            cv2.imwrite(str(prep_path), tile_img)
            debug_files.append(prep_path.name)
            json_path = debug_root / f"{image.stem}__resp_{index}.json"
        else:
            json_path = None
        text = ollama_ocr_one_image(
            ollama_generate_url=selected_url,
            model=selected_model,
            image_b64=b64_png(tile_img),
            prompt=selected_prompt,
            timeout_s=timeout_s,
            num_predict=max_new_tokens,
            debug_json_path=json_path,
        )
        if debug_root:
            raw_path = debug_root / f"{image.stem}__raw_{index}.txt"
            raw_path.write_text(text, encoding="utf-8")
            debug_files.append(raw_path.name)
            if json_path:
                debug_files.append(json_path.name)
        if not is_effectively_empty(text):
            texts.append(text)

    if not texts and tile:
        json_path = debug_root / f"{image.stem}__resp_full.json" if debug_root else None
        text = ollama_ocr_one_image(
            ollama_generate_url=selected_url,
            model=selected_model,
            image_b64=b64_png(prep.ocr_bgr),
            prompt=selected_prompt,
            timeout_s=timeout_s,
            num_predict=max_new_tokens,
            debug_json_path=json_path,
        )
        if debug_root:
            raw_path = debug_root / f"{image.stem}__raw_full.txt"
            raw_path.write_text(text, encoding="utf-8")
            debug_files.append(raw_path.name)
            if json_path:
                debug_files.append(json_path.name)
        if not is_effectively_empty(text):
            texts.append(text)

    final = "\n\n".join(texts).strip()
    if is_effectively_empty(final):
        final = "[OCR_EMPTY]"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(final, encoding="utf-8")
    elapsed = round(time.time() - page_start, 2)
    return OcrResult(
        image_path=image,
        out_file=output,
        text=final,
        status="done",
        elapsed_seconds=elapsed,
        model=selected_model,
        tile_count=len(prep.tiles_bgr),
        debug_files=debug_files,
    )


def run_folder(
    input_dir: str | Path,
    out_dir: str | Path,
    *,
    model: str | None = None,
    ollama_generate_url: str | None = None,
    resume: bool = True,
    debug: bool = True,
    tile: bool = True,
    max_new_tokens: int = 1200,
    prompt: str | None = None,
    timeout_s: int = 240,
    progress: ProgressCallback | None = None,
    wait_ready: bool = True,
) -> dict[str, Any]:
    input_path = Path(input_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    selected_model = model or settings.OCR_MODEL
    selected_url = ollama_generate_url or settings.OLLAMA_URL
    if wait_ready:
        wait_for_ollama_ready(selected_url, timeout_s=240)

    images = sorted(path for path in input_path.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"})
    manifest = _initial_manifest(output_path.name, input_path, output_path, selected_model, tile, max_new_tokens, len(images))
    write_json_atomic(output_path / "manifest.json", manifest)
    log_path = output_path / "run_status.log"
    log_path.write_text(f"=== OCR run {manifest['created_at']} model={selected_model} pages={len(images)} ===\n", encoding="utf-8")

    for index, image in enumerate(images, start=1):
        out_file = output_path / f"{image.stem}.txt"
        page = _page_number(image)
        page_meta = _page_entry(manifest, page, image.name)
        if resume and should_skip_existing(out_file):
            page_meta.update(_status_for_file(out_file, image.name, page, selected_model, "skipped"))
            _refresh_manifest(manifest, output_path)
            write_json_atomic(output_path / "manifest.json", manifest)
            _append_log(log_path, f"[SKIP] {index:03d}/{len(images):03d} {image.name}")
            if progress:
                progress("skip", page, len(images), image)
            continue
        start = time.time()
        try:
            page_meta.update({"status": "running", "updated_at": _utc_now(), "error": ""})
            write_json_atomic(output_path / "manifest.json", manifest)
            result = ocr_page(
                image,
                out_file,
                model=selected_model,
                ollama_generate_url=selected_url,
                prompt=prompt,
                tile=tile,
                max_new_tokens=max_new_tokens,
                timeout_s=timeout_s,
                debug_dir=output_path / "_debug" if debug else None,
            )
            page_meta.update(_status_for_file(out_file, image.name, page, selected_model, "done", result.elapsed_seconds, result.tile_count))
            _append_log(log_path, f"[OK ] {index:03d}/{len(images):03d} {image.name} ({result.elapsed_seconds}s) chars={len(result.text)}")
            if progress:
                progress("done", page, len(images), image)
        except Exception as exc:
            out_file.write_text("[OCR_EMPTY]", encoding="utf-8")
            page_meta.update(_status_for_file(out_file, image.name, page, selected_model, "error", round(time.time() - start, 2), 0))
            page_meta["error"] = str(exc)
            if debug:
                debug_dir = output_path / "_debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"{image.stem}__error.txt").write_text(str(exc), encoding="utf-8")
            _append_log(log_path, f"[FAIL] {index:03d}/{len(images):03d} {image.name} {exc}")
            if progress:
                progress("error", page, len(images), image)
        _refresh_manifest(manifest, output_path)
        write_json_atomic(output_path / "manifest.json", manifest)

    _refresh_manifest(manifest, output_path)
    write_json_atomic(output_path / "manifest.json", manifest)
    return manifest


def _initial_manifest(doc_id: str, in_dir: Path, out_dir: Path, model: str, tile: bool, max_new_tokens: int, total_pages: int) -> dict[str, Any]:
    now = _utc_now()
    existing = _read_manifest(out_dir / "manifest.json")
    return {
        "doc_id": doc_id,
        "input_dir": str(in_dir),
        "out_dir": str(out_dir),
        "model": model,
        "tile": tile,
        "max_new_tokens": max_new_tokens,
        "status": existing.get("status", "processing") if existing else "processing",
        "total_pages": total_pages,
        "completed_pages": existing.get("completed_pages", 0) if existing else 0,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "pages": existing.get("pages", []) if existing else [],
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _page_entry(manifest: dict[str, Any], page: int, filename: str) -> dict[str, Any]:
    for entry in manifest["pages"]:
        if entry.get("page") == page:
            entry["filename"] = filename
            return entry
    entry = {"page": page, "filename": filename, "status": "pending"}
    manifest["pages"].append(entry)
    manifest["pages"].sort(key=lambda item: int(item.get("page", 0)))
    return entry


def _status_for_file(out_file: Path, filename: str, page: int, model: str, status: str, elapsed: float | str = "", tile_count: int = 0) -> dict[str, Any]:
    text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    return {
        "page": page,
        "filename": filename,
        "text_file": out_file.name,
        "status": status,
        "char_count": len(text),
        "model": model,
        "tile_count": tile_count,
        "elapsed_seconds": elapsed,
        "updated_at": _utc_now(),
        "error": "",
    }


def _refresh_manifest(manifest: dict[str, Any], out_dir: Path) -> None:
    completed = 0
    errors = 0
    for entry in manifest.get("pages", []):
        status = entry.get("status")
        if status in {"done", "skipped"}:
            completed += 1
        elif status == "error":
            errors += 1
    manifest["completed_pages"] = completed
    manifest["updated_at"] = _utc_now()
    total = manifest.get("total_pages", 0)
    if total and completed == total:
        manifest["status"] = "complete"
    elif errors:
        manifest["status"] = "partial_with_errors"
    else:
        manifest["status"] = "partial" if completed else "processing"


def _append_log(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _page_number(path: Path) -> int:
    match = re.search(r"p(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0
