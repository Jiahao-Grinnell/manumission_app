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
from shared.schemas import CallStats, PageDecision
from shared.storage import artifact_ok, write_json_atomic
from shared.text_utils import clean_ocr, render_prompt

from .parsing import choose_report_type, parse_page_decision
from .rules import collect_rule_hints, explain_override


DEFAULT_PAGE_CLASSIFY_PROMPT = """You are reading ONE OCR page from a historical slavery / manumission archive.

Your task is to decide whether this page should be extracted, and if so infer the page-level report type.

Return JSON only:
{
  "should_extract": true,
  "skip_reason": null,
  "report_type": "statement",
  "evidence": "..."
}

Allowed skip_reason values:
- null
- index
- record_metadata
- bad_ocr

Allowed report_type values:
- statement
- correspondence

Definitions:
- statement: a recorded testimony, declaration, or first-person account.
- correspondence: any official letter, telegram, memo, recommendation, forwarding note, or investigative office communication.

Critical rule:
- Classify by the MAIN FUNCTION of the page, not by document form.
- Use statement only for recorded testimony, declaration, or first-person account pages.
- Use correspondence for all other extractable official communications, including forwarding, recommendation, logistics, repatriation handling, certificate handling, and office discussion.

Decision hints:
- Choose statement for "Statement of...", "I was born...", "I was kidnapped...", "I request...", recorded testimony, or declarations.
- Choose correspondence for official letters, telegrams, memoranda, recommendations, forwarding notes, investigative discussion, repatriation requests/arrangements, passage, delivery to a place, maintenance, or certificate handling.

Skip only when the page is clearly one of these:
- index or list page
- archive metadata / cover / about-this-record page
- OCR too damaged to extract reliably

Important:
- Use ONLY this page.
- Do not decide skip_reason merely because the page is short or administrative.
- Administrative cover letters that still name manumission subjects should still be extracted.
- evidence must be a short quote or phrase from the page, max 25 words.
- Output JSON only.

OCR TEXT:
<<<{ocr}>>>"""

SCHEMA_HINT = '{"should_extract":true,"skip_reason":null,"report_type":"statement","evidence":"..."}'
ProgressCallback = Callable[[str, int, int, Path], None]


@dataclass(frozen=True)
class ClassificationResult:
    page: int
    should_extract: bool
    skip_reason: str | None
    report_type: str
    evidence: str
    model_calls: int
    repair_calls: int
    elapsed_seconds: float
    raw_decision: dict[str, Any]
    initial_decision: dict[str, Any]
    override: dict[str, Any]
    rule_hints: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "should_extract": self.should_extract,
            "skip_reason": self.skip_reason,
            "report_type": self.report_type,
            "evidence": self.evidence,
            "model_calls": self.model_calls,
            "repair_calls": self.repair_calls,
            "elapsed_seconds": self.elapsed_seconds,
            "raw_decision": self.raw_decision,
            "initial_decision": self.initial_decision,
            "override": self.override,
            "rule_hints": self.rule_hints,
        }


def load_prompt(prompt: str | None = None, *, prompt_path: Path | None = None) -> str:
    if prompt:
        return prompt.strip()
    if prompt_path is not None and prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return load_prompt_text(
        "page_classifier",
        "page_classify.txt",
        legacy_names=["page_classify.txt"],
        fallback_text=DEFAULT_PAGE_CLASSIFY_PROMPT,
    )


def classify(
    ocr_text: str,
    *,
    client: OllamaClient | None = None,
    stats: CallStats | None = None,
    report_type_override: str | None = None,
    prompt: str | None = None,
) -> ClassificationResult:
    started = time.time()
    prepared_text = clean_ocr(ocr_text or "")
    call_stats = stats or CallStats()

    if report_type_override:
        chosen = choose_report_type(report_type_override)
        parsed = PageDecision(should_extract=True, skip_reason=None, report_type=chosen, evidence="override")
        raw_decision: dict[str, Any] = {
            "should_extract": True,
            "skip_reason": None,
            "report_type": chosen,
            "evidence": "override",
        }
        override = {
            "from": chosen,
            "to": chosen,
            "applied": False,
            "applied_by": "forced_report_type",
            "rules": collect_rule_hints(prepared_text),
        }
    else:
        selected_client = client or OllamaClient()
        obj = selected_client.generate_json(
            render_prompt(load_prompt(prompt), ocr=prepared_text),
            SCHEMA_HINT,
            call_stats,
            num_predict=500,
        )
        raw_decision = obj if isinstance(obj, dict) else {"raw": obj}
        parsed = parse_page_decision(obj)
        override = explain_override(prepared_text, parsed.report_type)

    final_decision = PageDecision(
        should_extract=parsed.should_extract,
        skip_reason=parsed.skip_reason,
        report_type=override["to"],
        evidence=parsed.evidence,
    )
    elapsed = round(time.time() - started, 2)
    page = _page_number_from_text(prepared_text)
    return ClassificationResult(
        page=page,
        should_extract=final_decision.should_extract,
        skip_reason=final_decision.skip_reason,
        report_type=final_decision.report_type,
        evidence=final_decision.evidence,
        model_calls=call_stats.model_calls,
        repair_calls=call_stats.repair_calls,
        elapsed_seconds=elapsed,
        raw_decision=raw_decision,
        initial_decision={
            "should_extract": parsed.should_extract,
            "skip_reason": parsed.skip_reason,
            "report_type": parsed.report_type,
            "evidence": parsed.evidence,
        },
        override=override,
        rule_hints=override["rules"],
    )


def classify_file(
    text_path: str | Path,
    out_path: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    report_type_override: str | None = None,
    prompt: str | None = None,
) -> ClassificationResult:
    source = Path(text_path)
    target = Path(out_path)
    text = source.read_text(encoding="utf-8", errors="replace")
    selected_client = client
    if selected_client is None and not report_type_override:
        selected_client = OllamaClient(model=model)
    result = classify(
        text,
        client=selected_client,
        report_type_override=report_type_override,
        prompt=prompt,
    )
    page = _page_number(source)
    stored = result.as_dict()
    stored["page"] = page
    write_json_atomic(target, stored)
    return ClassificationResult(
        page=page,
        should_extract=result.should_extract,
        skip_reason=result.skip_reason,
        report_type=result.report_type,
        evidence=result.evidence,
        model_calls=result.model_calls,
        repair_calls=result.repair_calls,
        elapsed_seconds=result.elapsed_seconds,
        raw_decision=result.raw_decision,
        initial_decision=result.initial_decision,
        override=result.override,
        rule_hints=result.rule_hints,
    )


def run_folder(
    input_dir: str | Path,
    out_dir: str | Path,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
    report_type_override: str | None = None,
    prompt: str | None = None,
    resume: bool = True,
    wait_ready: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    input_path = Path(input_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    selected_client = client
    if selected_client is None and not report_type_override:
        selected_client = OllamaClient(model=model)
    if selected_client is not None and wait_ready:
        selected_client.wait_ready(timeout_s=240)

    text_files = sorted(path for path in input_path.glob("p*.txt") if path.is_file())
    summary_pages: list[dict[str, Any]] = []
    completed = 0
    skipped = 0
    errors = 0

    for index, text_path in enumerate(text_files, start=1):
        page = _page_number(text_path)
        out_path = output_path / f"{text_path.stem}.classify.json"
        if resume and artifact_ok(out_path, "json"):
            skipped += 1
            summary_pages.append(_summary_row(page, out_path, status="skipped"))
            if progress:
                progress("skip", page, len(text_files), text_path)
            continue
        try:
            result = classify_file(
                text_path,
                out_path,
                client=selected_client,
                model=model,
                report_type_override=report_type_override,
                prompt=prompt,
            )
            completed += 1
            summary_pages.append(
                {
                    "page": page,
                    "status": "done",
                    "report_type": result.report_type,
                    "should_extract": result.should_extract,
                    "skip_reason": result.skip_reason,
                    "filename": out_path.name,
                }
            )
            if progress:
                progress("done", page, len(text_files), text_path)
        except Exception as exc:
            errors += 1
            summary_pages.append(
                {
                    "page": page,
                    "status": "error",
                    "error": str(exc),
                    "filename": out_path.name,
                }
            )
            if progress:
                progress("error", page, len(text_files), text_path)

    return {
        "doc_id": output_path.name,
        "input_dir": str(input_path),
        "out_dir": str(output_path),
        "model": model or settings.OLLAMA_MODEL,
        "report_type_override": report_type_override or "",
        "total_pages": len(text_files),
        "completed_pages": completed,
        "skipped_pages": skipped,
        "error_pages": errors,
        "status": _summary_status(len(text_files), completed, skipped, errors),
        "created_at": _utc_now(),
        "pages": summary_pages,
    }


def _summary_row(page: int, out_path: Path, *, status: str) -> dict[str, Any]:
    record = _read_json_dict(out_path)
    return {
        "page": page,
        "status": status,
        "report_type": choose_report_type(str(record.get("report_type") or "correspondence")),
        "should_extract": record.get("should_extract"),
        "skip_reason": record.get("skip_reason"),
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
