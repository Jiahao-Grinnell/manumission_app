from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from modules.normalizer.names import normalize_name
from shared.config import settings
from shared.ollama_client import OllamaClient
from shared.schemas import CallStats
from shared.storage import artifact_ok, write_json_atomic
from shared.text_utils import clean_ocr

from .merging import merge_name_candidates, names_maybe_same_person
from .passes import run_filter, run_pass1, run_recall, run_verify
from .rules import apply_rule_filter, clean_evidence


ProgressCallback = Callable[[str, int, int, Path], None]

RERUN_ALLOWED = ("pass1", "pass1_filter", "recall", "recall_filter", "verify")
STAGE_LABELS = {
    "pass1": "Pass 1 raw",
    "pass1_filter": "Pass 1 filter",
    "recall": "Recall raw",
    "recall_filter": "Recall filter",
    "merged": "Merged",
    "verify": "Verify",
    "rule_filter": "Rule filter",
}
RERUN_EFFECTS = {
    "pass1": {"pass1", "pass1_filter", "verify"},
    "pass1_filter": {"pass1_filter", "verify"},
    "recall": {"recall", "recall_filter", "verify"},
    "recall_filter": {"recall_filter", "verify"},
    "verify": {"verify"},
}


@dataclass(frozen=True)
class NameExtractionResult:
    page: int
    report_type: str
    named_people: list[dict[str, str]]
    passes: dict[str, dict[str, Any]]
    removed_candidates: list[dict[str, Any]]
    final_reasons: list[dict[str, Any]]
    model_calls: int
    repair_calls: int
    elapsed_seconds: float
    classify: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "report_type": self.report_type,
            "classify": self.classify,
            "named_people": self.named_people,
            "passes": self.passes,
            "removed_candidates": self.removed_candidates,
            "final_reasons": self.final_reasons,
            "model_calls": self.model_calls,
            "repair_calls": self.repair_calls,
            "elapsed_seconds": self.elapsed_seconds,
        }


def extract_names(
    ocr_text: str,
    *,
    report_type: str = "correspondence",
    classify_record: dict[str, Any] | None = None,
    client: OllamaClient | None = None,
    stats: CallStats | None = None,
    start_stage: str | None = None,
    existing_result: dict[str, Any] | None = None,
) -> NameExtractionResult:
    if start_stage is not None and start_stage not in RERUN_ALLOWED:
        raise ValueError(f"Unsupported rerun stage: {start_stage}")

    prepared_text = clean_ocr(ocr_text or "")
    call_stats = stats or CallStats()
    started = time.time()
    selected_client = client or OllamaClient()
    existing_passes = _existing_pass_map(existing_result)

    pass1_stage = (
        run_pass1(selected_client, prepared_text, call_stats).as_dict()
        if _should_compute("pass1", start_stage, existing_passes)
        else _restore_stage(existing_passes["pass1"], "pass1")
    )
    pass1_filter_stage = (
        run_filter(
            selected_client,
            prepared_text,
            pass1_stage["candidates"],
            call_stats,
            stage="pass1_filter",
            label=STAGE_LABELS["pass1_filter"],
        ).as_dict()
        if _should_compute("pass1_filter", start_stage, existing_passes)
        else _restore_stage(existing_passes["pass1_filter"], "pass1_filter")
    )
    recall_stage = (
        run_recall(selected_client, prepared_text, call_stats).as_dict()
        if _should_compute("recall", start_stage, existing_passes)
        else _restore_stage(existing_passes["recall"], "recall")
    )
    recall_filter_stage = (
        run_filter(
            selected_client,
            prepared_text,
            recall_stage["candidates"],
            call_stats,
            stage="recall_filter",
            label=STAGE_LABELS["recall_filter"],
        ).as_dict()
        if _should_compute("recall_filter", start_stage, existing_passes)
        else _restore_stage(existing_passes["recall_filter"], "recall_filter")
    )

    merged_stage = _build_merged_stage(pass1_filter_stage["candidates"], recall_filter_stage["candidates"])
    verify_stage = (
        run_verify(selected_client, prepared_text, merged_stage["candidates"], call_stats).as_dict()
        if _should_compute("verify", start_stage, existing_passes)
        else _restore_stage(existing_passes["verify"], "verify")
    )
    rule_filter_stage, final_people, final_reasons = _build_rule_stage(verify_stage["candidates"], prepared_text)

    passes = {
        "pass1": pass1_stage,
        "pass1_filter": _attach_removed(
            pass1_filter_stage,
            _subset_removed(
                pass1_filter_stage["input_candidates"],
                pass1_filter_stage["candidates"],
                stage="pass1_filter",
                reason_type="model_filter",
                reason="Removed by the pass-1 filter stage.",
            ),
        ),
        "recall": recall_stage,
        "recall_filter": _attach_removed(
            recall_filter_stage,
            _subset_removed(
                recall_filter_stage["input_candidates"],
                recall_filter_stage["candidates"],
                stage="recall_filter",
                reason_type="model_filter",
                reason="Removed by the recall filter stage.",
            ),
        ),
        "merged": merged_stage,
        "verify": _attach_removed(
            verify_stage,
            _subset_removed(
                verify_stage["input_candidates"],
                verify_stage["candidates"],
                stage="verify",
                reason_type="model_verify",
                reason="Removed by the verify stage.",
            ),
        ),
        "rule_filter": rule_filter_stage,
    }

    removed_candidates = []
    for stage_name in ("pass1_filter", "recall_filter", "merged", "verify", "rule_filter"):
        removed_candidates.extend(passes[stage_name].get("removed", []))

    elapsed = round(time.time() - started, 2)
    return NameExtractionResult(
        page=_page_number_from_text(prepared_text),
        report_type=report_type,
        named_people=final_people,
        passes=passes,
        removed_candidates=removed_candidates,
        final_reasons=final_reasons,
        model_calls=call_stats.model_calls,
        repair_calls=call_stats.repair_calls,
        elapsed_seconds=elapsed,
        classify=classify_record or {},
    )


def extract_file(
    text_path: str | Path,
    classify_path: str | Path,
    out_path: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    start_stage: str | None = None,
    existing_result: dict[str, Any] | None = None,
) -> NameExtractionResult:
    source = Path(text_path)
    classify_file = Path(classify_path)
    target = Path(out_path)
    text = source.read_text(encoding="utf-8", errors="replace")
    classify_record = _load_extractable_classify(classify_file)
    selected_client = client or OllamaClient(model=model)
    result = extract_names(
        text,
        report_type=str(classify_record.get("report_type") or "correspondence"),
        classify_record=classify_record,
        client=selected_client,
        start_stage=start_stage,
        existing_result=existing_result,
    )
    stored = result.as_dict()
    stored["page"] = _page_number(source)
    write_json_atomic(target, stored)
    return NameExtractionResult(
        page=stored["page"],
        report_type=result.report_type,
        named_people=result.named_people,
        passes=result.passes,
        removed_candidates=result.removed_candidates,
        final_reasons=result.final_reasons,
        model_calls=result.model_calls,
        repair_calls=result.repair_calls,
        elapsed_seconds=result.elapsed_seconds,
        classify=result.classify,
    )


def rerun_pass_file(
    text_path: str | Path,
    classify_path: str | Path,
    out_path: str | Path,
    pass_name: str,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
) -> NameExtractionResult:
    if pass_name not in RERUN_ALLOWED:
        raise ValueError(f"Unsupported rerun stage: {pass_name}")
    existing_result = _read_json_dict(Path(out_path))
    return extract_file(
        text_path,
        classify_path,
        out_path,
        client=client,
        model=model,
        start_stage=pass_name if existing_result else None,
        existing_result=existing_result or None,
    )


def run_folder(
    input_dir: str | Path,
    classify_dir: str | Path,
    out_dir: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    resume: bool = True,
    wait_ready: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    input_path = Path(input_dir)
    classify_path = Path(classify_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    selected_client = client or OllamaClient(model=model)
    if wait_ready:
        selected_client.wait_ready(timeout_s=240)

    extractable_pages = _extractable_pages(input_path, classify_path)
    completed = 0
    skipped = 0
    errors = 0
    summary_pages: list[dict[str, Any]] = []

    for index, item in enumerate(extractable_pages, start=1):
        page = item["page"]
        out_file = output_path / f"p{page:03d}.names.json"
        if resume and artifact_ok(out_file, "json"):
            skipped += 1
            summary_pages.append(_summary_row(page, out_file, status="skipped"))
            if progress:
                progress("skip", page, len(extractable_pages), item["text_path"])
            continue
        try:
            result = extract_file(
                item["text_path"],
                item["classify_path"],
                out_file,
                client=selected_client,
                model=model,
            )
            completed += 1
            summary_pages.append(
                {
                    "page": page,
                    "status": "done",
                    "named_people": len(result.named_people),
                    "report_type": result.report_type,
                    "model_calls": result.model_calls,
                    "repair_calls": result.repair_calls,
                    "filename": out_file.name,
                }
            )
            if progress:
                progress("done", page, len(extractable_pages), item["text_path"])
        except Exception as exc:
            errors += 1
            summary_pages.append(
                {
                    "page": page,
                    "status": "error",
                    "error": str(exc),
                    "filename": out_file.name,
                }
            )
            if progress:
                progress("error", page, len(extractable_pages), item["text_path"])

    return {
        "doc_id": output_path.name,
        "input_dir": str(input_path),
        "classify_dir": str(classify_path),
        "out_dir": str(output_path),
        "model": model or settings.OLLAMA_MODEL,
        "total_pages": len(extractable_pages),
        "completed_pages": completed,
        "skipped_pages": skipped,
        "error_pages": errors,
        "status": _summary_status(len(extractable_pages), completed, skipped, errors),
        "created_at": _utc_now(),
        "pages": summary_pages,
    }


def _build_merged_stage(pass1_filtered: list[dict[str, str]], recall_filtered: list[dict[str, str]]) -> dict[str, Any]:
    input_candidates = [dict(item) for item in [*pass1_filtered, *recall_filtered]]
    merged_candidates = merge_name_candidates(pass1_filtered, recall_filtered)
    return {
        "stage": "merged",
        "label": STAGE_LABELS["merged"],
        "input_candidates": input_candidates,
        "llm_candidates": [],
        "candidates": merged_candidates,
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
        "removed": _merged_removed(input_candidates, merged_candidates),
    }


def _build_rule_stage(candidates: list[dict[str, str]], ocr: str) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    final_people, removed, kept_reasons = apply_rule_filter(candidates, ocr)
    stage = {
        "stage": "rule_filter",
        "label": STAGE_LABELS["rule_filter"],
        "input_candidates": [dict(item) for item in candidates],
        "llm_candidates": [],
        "candidates": final_people,
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
        "removed": removed,
        "kept_reasons": kept_reasons,
    }
    return stage, final_people, kept_reasons


def _attach_removed(stage: dict[str, Any], removed: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(stage)
    updated["removed"] = removed
    return updated


def _subset_removed(
    input_candidates: list[dict[str, str]],
    output_candidates: list[dict[str, str]],
    *,
    stage: str,
    reason_type: str,
    reason: str,
) -> list[dict[str, Any]]:
    output_keys = {normalize_name(item.get("name", "")).lower() for item in output_candidates}
    removed: list[dict[str, Any]] = []
    for item in input_candidates:
        key = normalize_name(item.get("name", "")).lower()
        if key and key not in output_keys:
            removed.append(
                {
                    "name": item.get("name", ""),
                    "evidence": clean_evidence(item.get("evidence", "")),
                    "stage": stage,
                    "reason_type": reason_type,
                    "reason": reason,
                    "excerpt": clean_evidence(item.get("evidence", "")),
                }
            )
    return removed


def _merged_removed(input_candidates: list[dict[str, str]], merged_candidates: list[dict[str, str]]) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    output_counts = Counter(normalize_name(item.get("name", "")).lower() for item in merged_candidates)
    seen_output: Counter[str] = Counter()

    for item in input_candidates:
        key = normalize_name(item.get("name", "")).lower()
        if not key:
            continue
        if seen_output[key] < output_counts[key]:
            seen_output[key] += 1
            continue
        removed.append(
            {
                "name": item.get("name", ""),
                "evidence": clean_evidence(item.get("evidence", "")),
                "stage": "merged",
                "reason_type": "merged_duplicate",
                "reason": "Collapsed an exact duplicate during merge.",
                "excerpt": clean_evidence(item.get("evidence", "")),
            }
        )

    for item in input_candidates:
        key = normalize_name(item.get("name", "")).lower()
        if not key or any(
            existing["name"] == item.get("name", "") and existing["evidence"] == clean_evidence(item.get("evidence", ""))
            for existing in removed
        ):
            continue
        if key in output_counts:
            continue
        merged_into = next(
            (
                output["name"]
                for output in merged_candidates
                if names_maybe_same_person(item.get("name", ""), output.get("name", ""))
            ),
            "",
        )
        if merged_into:
            removed.append(
                {
                    "name": item.get("name", ""),
                    "evidence": clean_evidence(item.get("evidence", "")),
                    "stage": "merged",
                    "reason_type": "merged_variant",
                    "reason": f'Merged into "{merged_into}".',
                    "excerpt": clean_evidence(item.get("evidence", "")),
                    "kept_as": merged_into,
                }
            )
    return removed


def _should_compute(stage: str, start_stage: str | None, existing_passes: dict[str, dict[str, Any]]) -> bool:
    if stage not in existing_passes:
        return True
    if start_stage is None:
        return True
    return stage in RERUN_EFFECTS[start_stage]


def _restore_stage(record: dict[str, Any], stage: str) -> dict[str, Any]:
    restored = dict(record)
    restored.setdefault("stage", stage)
    restored.setdefault("label", STAGE_LABELS[stage])
    restored.setdefault("input_candidates", [])
    restored.setdefault("llm_candidates", list(restored.get("candidates") or []))
    restored.setdefault("candidates", [])
    restored.setdefault("prompt_name", "")
    restored.setdefault("rendered_prompt", "")
    restored.setdefault("response_json", {})
    restored.setdefault("fallback_applied", False)
    restored.setdefault("fallback_reason", "")
    restored.setdefault("removed", [])
    return restored


def _existing_pass_map(existing_result: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not existing_result:
        return {}
    passes = existing_result.get("passes")
    return passes if isinstance(passes, dict) else {}


def _load_extractable_classify(path: Path) -> dict[str, Any]:
    record = _read_json_dict(path)
    if not record:
        raise FileNotFoundError(f"Missing classify artifact: {path}")
    if not record.get("should_extract"):
        raise ValueError(f"Page is not extractable according to {path.name}")
    return {
        "should_extract": True,
        "skip_reason": None,
        "report_type": str(record.get("report_type") or "correspondence"),
        "evidence": clean_evidence(record.get("evidence", "")),
    }


def _extractable_pages(input_dir: Path, classify_dir: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for text_path in sorted(path for path in input_dir.glob("p*.txt") if path.is_file()):
        page = _page_number(text_path)
        classify_path = classify_dir / f"p{page:03d}.classify.json"
        record = _read_json_dict(classify_path)
        if not record.get("should_extract"):
            continue
        pages.append({"page": page, "text_path": text_path, "classify_path": classify_path, "report_type": record.get("report_type")})
    return pages


def _summary_row(page: int, out_path: Path, *, status: str) -> dict[str, Any]:
    record = _read_json_dict(out_path)
    return {
        "page": page,
        "status": status,
        "named_people": len(record.get("named_people") or []),
        "report_type": record.get("report_type"),
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _page_number(path: Path) -> int:
    match = re.search(r"p(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def _page_number_from_text(text: str) -> int:
    match = re.search(r"\((\d+)/\d+\)", text or "")
    return int(match.group(1)) if match else 0
