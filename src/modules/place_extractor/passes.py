from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.ollama_client import OllamaClient
from shared.prompt_loader import load_prompt_text
from shared.schemas import CallStats
from shared.text_utils import render_prompt

from .parsing import parse_candidate_places, parse_place_rows, serialize_place_rows


DEFAULT_PROMPTS = {
    "pass": "place_pass.txt",
    "recall": "place_recall.txt",
    "verify": "place_verify.txt",
    "date_enrich": "place_date_enrich.txt",
}


@dataclass
class PlaceStageOutput:
    stage: str
    label: str
    prompt_name: str
    rendered_prompt: str
    response_json: dict[str, Any]
    input_rows: list[dict[str, Any]]
    llm_rows: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    fallback_applied: bool = False
    fallback_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "label": self.label,
            "prompt_name": self.prompt_name,
            "rendered_prompt": self.rendered_prompt,
            "response_json": self.response_json,
            "input_rows": self.input_rows,
            "llm_rows": self.llm_rows,
            "rows": self.rows,
            "fallback_applied": self.fallback_applied,
            "fallback_reason": self.fallback_reason,
        }


def load_prompt(kind: str, *, prompt: str | None = None, prompt_path: Path | None = None) -> str:
    if prompt:
        return prompt.strip()
    if prompt_path is not None and prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return load_prompt_text("place_extractor", DEFAULT_PROMPTS[kind], fallback_text="")


def run_candidate_pass(
    client: OllamaClient,
    ocr: str,
    name: str,
    page: int,
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> PlaceStageOutput:
    prompt_text = load_prompt("pass", prompt=prompt)
    rendered_prompt = render_prompt(prompt_text, name=name, ocr=ocr)
    obj = client.generate_json(rendered_prompt, _candidate_schema_hint(name), stats, num_predict=1000)
    rows = parse_candidate_places(obj, name, page)
    return PlaceStageOutput(
        stage="pass1",
        label="Pass 1",
        prompt_name=DEFAULT_PROMPTS["pass"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_rows=[],
        llm_rows=rows,
        rows=rows,
    )


def run_recall_pass(
    client: OllamaClient,
    ocr: str,
    name: str,
    page: int,
    doc_year: int | None,
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> PlaceStageOutput:
    prompt_text = load_prompt("recall", prompt=prompt)
    rendered_prompt = render_prompt(prompt_text, name=name, ocr=ocr)
    obj = client.generate_json(rendered_prompt, _final_schema_hint(name), stats, num_predict=1000)
    rows = parse_place_rows(obj, name, page, doc_year)
    return PlaceStageOutput(
        stage="recall",
        label="Recall",
        prompt_name=DEFAULT_PROMPTS["recall"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_rows=[],
        llm_rows=rows,
        rows=rows,
    )


def run_verify_pass(
    client: OllamaClient,
    ocr: str,
    name: str,
    page: int,
    candidate_rows: list[dict[str, Any]],
    doc_year: int | None,
    stats: CallStats,
    *,
    issues: str = "",
    prompt: str | None = None,
) -> PlaceStageOutput:
    prompt_text = load_prompt("verify", prompt=prompt)
    candidate_payload = json.dumps(serialize_place_rows(candidate_rows), ensure_ascii=False, indent=2)
    if issues:
        candidate_payload = f"{candidate_payload}\n\nIssues to fix:\n- {issues}"
    rendered_prompt = render_prompt(prompt_text, name=name, page=page, candidate_places_json=candidate_payload, ocr=ocr)
    obj = client.generate_json(rendered_prompt, _final_schema_hint(name), stats, num_predict=1200)
    rows = parse_place_rows(obj, name, page, doc_year)
    return PlaceStageOutput(
        stage="verify",
        label="Verified",
        prompt_name=DEFAULT_PROMPTS["verify"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_rows=[dict(row) for row in candidate_rows],
        llm_rows=rows,
        rows=rows,
    )


def run_date_enrich_pass(
    client: OllamaClient,
    ocr: str,
    name: str,
    page: int,
    base_rows: list[dict[str, Any]],
    doc_year: int | None,
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> PlaceStageOutput:
    if not base_rows:
        return PlaceStageOutput(
            stage="date_enrich",
            label="Date Enrich",
            prompt_name=DEFAULT_PROMPTS["date_enrich"],
            rendered_prompt="",
            response_json={},
            input_rows=[],
            llm_rows=[],
            rows=[],
            fallback_applied=False,
            fallback_reason="Skipped because there were no base rows.",
        )
    prompt_text = load_prompt("date_enrich", prompt=prompt)
    places_payload = json.dumps(serialize_place_rows(base_rows), ensure_ascii=False, indent=2)
    rendered_prompt = render_prompt(prompt_text, name=name, places_json=places_payload, ocr=ocr)
    obj = client.generate_json(rendered_prompt, _final_schema_hint(name), stats, num_predict=900)
    rows = parse_place_rows(obj, name, page, doc_year)
    return PlaceStageOutput(
        stage="date_enrich",
        label="Date Enrich",
        prompt_name=DEFAULT_PROMPTS["date_enrich"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_rows=[dict(row) for row in base_rows],
        llm_rows=rows,
        rows=rows,
    )


def _response_json(obj: Any) -> dict[str, Any]:
    return obj if isinstance(obj, dict) else {"raw": obj}


def _candidate_schema_hint(name: str) -> str:
    return '{"name":"%s","places":[{"place":"...","time_text":null,"evidence":"..."}]}' % name


def _final_schema_hint(name: str) -> str:
    return (
        '{"name":"%s","places":[{"place":"...","order":0,"arrival_date":null,"date_confidence":"unknown","time_text":null,"evidence":"..."}]}'
        % name
    )
