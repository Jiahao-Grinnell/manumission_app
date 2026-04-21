from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.normalizer.names import normalize_name
from shared.ollama_client import OllamaClient
from shared.prompt_loader import load_prompt_text
from shared.schemas import CallStats
from shared.text_utils import render_prompt

from .merging import looks_like_candidate_name
from .rules import clean_evidence


DEFAULT_PROMPTS = {
    "pass1": "name_pass.txt",
    "recall": "name_recall.txt",
    "filter": "name_filter.txt",
    "verify": "name_verify.txt",
}

SCHEMA_HINT = '{"named_people":[{"name":"...","evidence":"..."}]}'


@dataclass
class ModelStageOutput:
    stage: str
    label: str
    prompt_name: str
    rendered_prompt: str
    response_json: dict[str, Any]
    llm_candidates: list[dict[str, str]]
    candidates: list[dict[str, str]]
    input_candidates: list[dict[str, str]]
    fallback_applied: bool = False
    fallback_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "label": self.label,
            "prompt_name": self.prompt_name,
            "rendered_prompt": self.rendered_prompt,
            "response_json": self.response_json,
            "input_candidates": self.input_candidates,
            "llm_candidates": self.llm_candidates,
            "candidates": self.candidates,
            "fallback_applied": self.fallback_applied,
            "fallback_reason": self.fallback_reason,
        }


def load_prompt(kind: str, *, prompt: str | None = None, prompt_path: Path | None = None) -> str:
    if prompt:
        return prompt.strip()
    if prompt_path is not None and prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return load_prompt_text("name_extractor", DEFAULT_PROMPTS[kind], fallback_text="")


def parse_named_people(obj: Any) -> list[dict[str, str]]:
    if not isinstance(obj, dict):
        return []
    merged: dict[str, dict[str, str]] = {}
    for item in obj.get("named_people") or []:
        if not isinstance(item, dict):
            continue
        name = normalize_name(str(item.get("name") or ""))
        if not looks_like_candidate_name(name):
            continue
        key = name.lower()
        evidence = clean_evidence(item.get("evidence"))
        current = merged.get(key)
        if current is None or len(name) > len(current["name"]) or len(evidence) > len(current["evidence"]):
            merged[key] = {"name": name, "evidence": evidence}
    return list(merged.values())


def run_pass1(
    client: OllamaClient,
    ocr: str,
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> ModelStageOutput:
    return _run_free_stage(
        client,
        ocr,
        stats,
        stage="pass1",
        label="Pass 1 raw",
        prompt_name=DEFAULT_PROMPTS["pass1"],
        prompt_text=load_prompt("pass1", prompt=prompt),
    )


def run_recall(
    client: OllamaClient,
    ocr: str,
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> ModelStageOutput:
    return _run_free_stage(
        client,
        ocr,
        stats,
        stage="recall",
        label="Recall raw",
        prompt_name=DEFAULT_PROMPTS["recall"],
        prompt_text=load_prompt("recall", prompt=prompt),
    )


def run_filter(
    client: OllamaClient,
    ocr: str,
    candidates: list[dict[str, str]],
    stats: CallStats,
    *,
    stage: str,
    label: str,
    prompt: str | None = None,
) -> ModelStageOutput:
    input_candidates = [dict(item) for item in candidates]
    payload = json.dumps(input_candidates, ensure_ascii=False, indent=2)
    prompt_text = load_prompt("filter", prompt=prompt)
    rendered_prompt = render_prompt(prompt_text, stage=stage, candidate_names_json=payload, ocr=ocr)
    obj = client.generate_json(rendered_prompt, SCHEMA_HINT, stats, num_predict=900)
    llm_candidates = _restrict_to_allowed(parse_named_people(obj), input_candidates)
    fallback_applied = bool(input_candidates) and not llm_candidates
    return ModelStageOutput(
        stage=stage,
        label=label,
        prompt_name=DEFAULT_PROMPTS["filter"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_candidates=input_candidates,
        llm_candidates=llm_candidates,
        candidates=llm_candidates or input_candidates,
        fallback_applied=fallback_applied,
        fallback_reason="Model returned no kept names; fell back to upstream candidates." if fallback_applied else "",
    )


def run_verify(
    client: OllamaClient,
    ocr: str,
    candidates: list[dict[str, str]],
    stats: CallStats,
    *,
    prompt: str | None = None,
) -> ModelStageOutput:
    input_candidates = [dict(item) for item in candidates]
    payload = json.dumps(input_candidates, ensure_ascii=False, indent=2)
    prompt_text = load_prompt("verify", prompt=prompt)
    rendered_prompt = render_prompt(prompt_text, candidate_names_json=payload, ocr=ocr)
    obj = client.generate_json(rendered_prompt, SCHEMA_HINT, stats, num_predict=900)
    llm_candidates = _restrict_to_allowed(parse_named_people(obj), input_candidates)
    fallback_applied = bool(input_candidates) and not llm_candidates
    return ModelStageOutput(
        stage="verify",
        label="Verify",
        prompt_name=DEFAULT_PROMPTS["verify"],
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_candidates=input_candidates,
        llm_candidates=llm_candidates,
        candidates=llm_candidates or input_candidates,
        fallback_applied=fallback_applied,
        fallback_reason="Verifier returned no names; fell back to merged candidates." if fallback_applied else "",
    )


def _run_free_stage(
    client: OllamaClient,
    ocr: str,
    stats: CallStats,
    *,
    stage: str,
    label: str,
    prompt_name: str,
    prompt_text: str,
) -> ModelStageOutput:
    rendered_prompt = render_prompt(prompt_text, ocr=ocr)
    obj = client.generate_json(rendered_prompt, SCHEMA_HINT, stats, num_predict=900)
    llm_candidates = parse_named_people(obj)
    return ModelStageOutput(
        stage=stage,
        label=label,
        prompt_name=prompt_name,
        rendered_prompt=rendered_prompt,
        response_json=_response_json(obj),
        input_candidates=[],
        llm_candidates=llm_candidates,
        candidates=llm_candidates,
    )


def _restrict_to_allowed(candidates: list[dict[str, str]], allowed: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed_by_name = {normalize_name(item.get("name", "")).lower(): item for item in allowed}
    filtered: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        key = normalize_name(item.get("name", "")).lower()
        if not key or key not in allowed_by_name or key in seen:
            continue
        original = allowed_by_name[key]
        filtered.append(
            {
                "name": original["name"],
                "evidence": clean_evidence(item.get("evidence") or original.get("evidence") or ""),
            }
        )
        seen.add(key)
    return filtered


def _response_json(obj: Any) -> dict[str, Any]:
    return obj if isinstance(obj, dict) else {"raw": obj}
