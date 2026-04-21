from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from shared.config import settings
from shared.ollama_client import OllamaClient
from shared.prompt_loader import load_prompt_text
from shared.schemas import CallStats
from shared.storage import artifact_ok, write_json_atomic
from shared.text_utils import clean_ocr, render_prompt

from .parsing import parse_meta
from .vocab import DETAIL_REPORT_TYPES


DEFAULT_META_PROMPT = """You are extracting structured case metadata for ONE named subject from ONE OCR page.

Return JSON only:
{
  "name": "{name}",
  "page": {page},
  "report_type": "statement",
  "crime_type": null,
  "whether_abuse": "",
  "conflict_type": null,
  "trial": null,
  "amount_paid": null,
  "evidence": {
    "crime_type": null,
    "whether_abuse": null,
    "conflict_type": null,
    "trial": null,
    "amount_paid": null
  }
}

SUBJECT NAME: {name}
PAGE: {page}
UPSTREAM PAGE REPORT TYPE: {report_type}

OCR TEXT:
<<<{ocr}>>>"""

ProgressCallback = Callable[[str, int, int, Path], None]


@dataclass(frozen=True)
class MetadataPersonResult:
    name: str
    row: dict[str, Any]
    validation: dict[str, Any]
    raw_values: dict[str, Any]
    rendered_prompt: str
    response_json: dict[str, Any]
    model_calls: int
    repair_calls: int
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row": self.row,
            "validation": self.validation,
            "raw_values": self.raw_values,
            "rendered_prompt": self.rendered_prompt,
            "response_json": self.response_json,
            "model_calls": self.model_calls,
            "repair_calls": self.repair_calls,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class MetadataPageResult:
    page: int
    report_type: str
    classify: dict[str, Any]
    names: list[str]
    people: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    model_calls: int
    repair_calls: int
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "report_type": self.report_type,
            "classify": self.classify,
            "names": self.names,
            "people": self.people,
            "rows": self.rows,
            "model_calls": self.model_calls,
            "repair_calls": self.repair_calls,
            "elapsed_seconds": self.elapsed_seconds,
        }


def load_prompt(prompt: str | None = None, *, prompt_path: Path | None = None) -> str:
    if prompt:
        base = prompt.strip()
    elif prompt_path is not None and prompt_path.exists():
        base = prompt_path.read_text(encoding="utf-8").strip()
    else:
        base = load_prompt_text(
            "metadata_extractor",
            "meta_pass.txt",
            legacy_names=["meta_pass.txt"],
            fallback_text=DEFAULT_META_PROMPT,
        )
    guide = load_prompt_text("metadata_extractor", "category_guide.txt", legacy_names=["category_guide.txt"], fallback_text="")
    if guide:
        return f"{base}\n\nCategory Guide:\n{guide}".strip()
    return base


def extract_person(
    ocr_text: str,
    name: str,
    page: int,
    report_type: str,
    *,
    classify_record: dict[str, Any] | None = None,
    client: OllamaClient | None = None,
    prompt: str | None = None,
) -> MetadataPersonResult:
    started = time.time()
    prepared_text = clean_ocr(ocr_text or "")
    selected_client = client or OllamaClient()
    stats = CallStats()
    rendered_prompt = render_prompt(load_prompt(prompt), name=name, page=page, report_type=report_type, ocr=prepared_text)
    obj = selected_client.generate_json(rendered_prompt, _schema_hint(name, page, report_type), stats, num_predict=1000)
    parsed = parse_meta(
        obj,
        name,
        page,
        report_type,
        classify_evidence=str((classify_record or {}).get("evidence") or ""),
    )
    return MetadataPersonResult(
        name=name,
        row=parsed["row"],
        validation=parsed["validation"],
        raw_values=parsed["raw_values"],
        rendered_prompt=rendered_prompt,
        response_json=obj if isinstance(obj, dict) else {"raw": obj},
        model_calls=stats.model_calls,
        repair_calls=stats.repair_calls,
        elapsed_seconds=round(time.time() - started, 2),
    )


def run_page_file(
    text_path: str | Path,
    classify_path: str | Path,
    names_path: str | Path,
    out_path: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    person_name: str | None = None,
) -> MetadataPageResult:
    source = Path(text_path)
    classify_file = Path(classify_path)
    names_file = Path(names_path)
    target = Path(out_path)

    ocr_text = source.read_text(encoding="utf-8", errors="replace")
    classify_record = _load_extractable_classify(classify_file)
    available_names = _load_names(names_file)
    if not available_names:
        raise ValueError(f"No named people found in {names_file.name}")

    selected_client = client or OllamaClient(model=model)
    page = _page_number(source)
    existing_people = _existing_people_map(_read_json_dict(target))

    if person_name:
        resolved_name = _resolve_name(person_name, available_names)
        person_result = extract_person(
            ocr_text,
            resolved_name,
            page,
            classify_record["report_type"],
            classify_record=classify_record,
            client=selected_client,
        )
        existing_people[resolved_name.casefold()] = person_result.as_dict()
        people_records = _ordered_people(available_names, list(existing_people.values()))
    else:
        people_records = [
            extract_person(
                ocr_text,
                current_name,
                page,
                classify_record["report_type"],
                classify_record=classify_record,
                client=selected_client,
            ).as_dict()
            for current_name in available_names
        ]

    page_result = _build_page_result(page, classify_record, available_names, people_records)
    write_json_atomic(target, page_result.as_dict())
    return page_result


def run_folder(
    input_dir: str | Path,
    inter_dir: str | Path,
    out_dir: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    resume: bool = True,
    wait_ready: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    input_path = Path(input_dir)
    intermediate_path = Path(inter_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    selected_client = client or OllamaClient(model=model)
    if wait_ready:
        selected_client.wait_ready(timeout_s=240)

    pages = _pages_with_names(input_path, intermediate_path)
    completed = 0
    skipped = 0
    errors = 0
    summary_pages: list[dict[str, Any]] = []

    for item in pages:
        page = item["page"]
        out_file = output_path / f"p{page:03d}.meta.json"
        if resume and artifact_ok(out_file, "json"):
            skipped += 1
            summary_pages.append(_summary_row(page, out_file, status="skipped"))
            if progress:
                progress("skip", page, len(pages), item["text_path"])
            continue
        try:
            result = run_page_file(
                item["text_path"],
                item["classify_path"],
                item["names_path"],
                out_file,
                client=selected_client,
                model=model,
            )
            completed += 1
            summary_pages.append(
                {
                    "page": page,
                    "status": "done",
                    "rows": len(result.rows),
                    "names": len(result.names),
                    "model_calls": result.model_calls,
                    "repair_calls": result.repair_calls,
                    "filename": out_file.name,
                }
            )
            if progress:
                progress("done", page, len(pages), item["text_path"])
        except Exception as exc:
            errors += 1
            summary_pages.append({"page": page, "status": "error", "error": str(exc), "filename": out_file.name})
            if progress:
                progress("error", page, len(pages), item["text_path"])

    return {
        "doc_id": output_path.name,
        "input_dir": str(input_path),
        "inter_dir": str(intermediate_path),
        "out_dir": str(output_path),
        "model": model or settings.OLLAMA_MODEL,
        "total_pages": len(pages),
        "completed_pages": completed,
        "skipped_pages": skipped,
        "error_pages": errors,
        "status": _summary_status(len(pages), completed, skipped, errors),
        "created_at": _utc_now(),
        "pages": summary_pages,
    }


def _build_page_result(
    page: int,
    classify_record: dict[str, Any],
    available_names: list[str],
    people_records: list[dict[str, Any]],
) -> MetadataPageResult:
    ordered_people = _ordered_people(available_names, people_records)
    rows = [dict(person["row"]) for person in ordered_people if isinstance(person.get("row"), dict)]
    model_calls = sum(_int_value(person.get("model_calls")) for person in ordered_people)
    repair_calls = sum(_int_value(person.get("repair_calls")) for person in ordered_people)
    elapsed_seconds = round(sum(_float_value(person.get("elapsed_seconds")) for person in ordered_people), 2)
    return MetadataPageResult(
        page=page,
        report_type=classify_record["report_type"],
        classify=classify_record,
        names=available_names,
        people=ordered_people,
        rows=rows,
        model_calls=model_calls,
        repair_calls=repair_calls,
        elapsed_seconds=elapsed_seconds,
    )


def _schema_hint(name: str, page: int, report_type: str) -> str:
    final_report_type = report_type if report_type in DETAIL_REPORT_TYPES else DETAIL_REPORT_TYPES[0]
    return (
        '{"name":"%s","page":%d,"report_type":"%s","crime_type":null,'
        '"whether_abuse":"","conflict_type":null,"trial":null,"amount_paid":null,'
        '"evidence":{"crime_type":null,"whether_abuse":null,"conflict_type":null,"trial":null,"amount_paid":null}}'
    ) % (name, page, final_report_type)


def _load_extractable_classify(path: Path) -> dict[str, Any]:
    record = _read_json_dict(path)
    if not record:
        raise FileNotFoundError(f"Missing classify artifact: {path}")
    if not record.get("should_extract"):
        raise ValueError(f"Page is not extractable according to {path.name}")
    report_type = str(record.get("report_type") or DETAIL_REPORT_TYPES[0])
    if report_type not in DETAIL_REPORT_TYPES:
        report_type = DETAIL_REPORT_TYPES[0]
    return {
        "should_extract": True,
        "skip_reason": None,
        "report_type": report_type,
        "evidence": str(record.get("evidence") or ""),
    }


def _load_names(path: Path) -> list[str]:
    record = _read_json_dict(path)
    raw = record.get("named_people") if isinstance(record, dict) else None
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
    return names


def _resolve_name(person_name: str, available_names: list[str]) -> str:
    lookup = {name.casefold(): name for name in available_names}
    resolved = lookup.get(str(person_name).strip().casefold())
    if not resolved:
        raise ValueError(f"Name {person_name!r} not found in page-level names list")
    return resolved


def _existing_people_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    people = record.get("people") if isinstance(record, dict) else None
    if not isinstance(people, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for person in people:
        if isinstance(person, dict):
            name = str(person.get("name") or "").strip()
            if name:
                out[name.casefold()] = dict(person)
    return out


def _ordered_people(available_names: list[str], people_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {str(person.get("name") or "").casefold(): dict(person) for person in people_records if str(person.get("name") or "").strip()}
    ordered: list[dict[str, Any]] = []
    for name in available_names:
        person = lookup.get(name.casefold())
        if person:
            ordered.append(person)
    for key, person in sorted(lookup.items(), key=lambda item: item[0]):
        if all(existing.get("name", "").casefold() != key for existing in ordered):
            ordered.append(person)
    return ordered


def _pages_with_names(input_dir: Path, inter_dir: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for text_path in sorted(path for path in input_dir.glob("p*.txt") if path.is_file()):
        page = _page_number(text_path)
        classify_path = inter_dir / f"p{page:03d}.classify.json"
        names_path = inter_dir / f"p{page:03d}.names.json"
        classify = _read_json_dict(classify_path)
        names = _load_names(names_path)
        if not classify.get("should_extract") or not names:
            continue
        pages.append({"page": page, "text_path": text_path, "classify_path": classify_path, "names_path": names_path})
    return pages


def _summary_row(page: int, out_path: Path, *, status: str) -> dict[str, Any]:
    record = _read_json_dict(out_path)
    return {
        "page": page,
        "status": status,
        "rows": len(record.get("rows") or []),
        "names": len(record.get("names") or []),
        "filename": out_path.name,
    }


def _summary_status(total: int, completed: int, skipped: int, errors: int) -> str:
    if total == 0:
        return "empty"
    if errors:
        return "partial_with_errors"
    if completed + skipped == total:
        return "complete"
    return "partial"


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _page_number(path: Path) -> int:
    match = re.search(r"p(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0

