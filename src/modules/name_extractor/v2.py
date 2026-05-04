from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from modules.normalizer.names import build_name_regex
from shared.prompt_loader import load_prompt_text
from shared.schemas import CallStats
from shared.text_utils import normalize_ws, render_prompt

from .merging import (
    canonicalize_candidate_name,
    is_generic_subject_phrase,
    looks_like_candidate_name,
    looks_like_subject_label,
    merge_name_candidates,
    names_maybe_same_person,
)
from .passes import parse_named_people
from .rules import clean_evidence, explain_candidate_decision, rule_seed_candidates
from .segments import TextSegment, build_segments, containing_segments, expanded_window, nearest_segment


MENTION_SCHEMA_HINT = '{"names":[{"name":"...","span_quote":"..."}]}'
ROLE_SCHEMA_HINT = '{"labels":[{"candidate_id":"cand_001","role":"ambiguous","confidence":"low","evidence_quote":"..."}]}'

POSITIVE_ROLES = {
    "enslaved_subject",
    "refugee_slave",
    "fugitive_slave",
    "manumission_applicant",
    "certificate_recipient",
    "kidnapped_victim",
    "recovered_person",
    "repatriated_person",
    "slave_status_investigation_subject",
    "relation_subject",
}
NEGATIVE_ROLES = {
    "owner",
    "master",
    "buyer",
    "seller",
    "broker",
    "kidnapper",
    "witness",
    "official",
    "correspondent",
    "signatory",
    "papers_source",
    "family_member_only",
    "freeborn_not_slave",
    "generic_unanchored",
}
ALLOWED_ROLES = POSITIVE_ROLES | NEGATIVE_ROLES | {"ambiguous"}
CONFIDENCE_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
SUBJECT_ACTION_RE = re.compile(
    r"\b(?:slave|slaves|refugee|fugitive|kidnapp(?:ed)?|captur(?:ed)?|stolen|sold|sale|"
    r"recover(?:ed)?|release(?:d)?|repatriat(?:ed|ion)|manumission|certificate|freedom|"
    r"took\s+bast|took\s+refuge|kept\s+as\s+a\s+slave|not\s+his\s+slave)\b",
    flags=re.I,
)


@dataclass
class V2PipelineOutput:
    passes: dict[str, dict[str, Any]]
    final_people: list[dict[str, str]]
    removed_candidates: list[dict[str, Any]]
    final_reasons: list[dict[str, Any]]
    elapsed_seconds: float


def run_v2_pipeline(
    ocr: str,
    *,
    report_type: str,
    classify_record: dict[str, Any],
    client: Any,
    stats: CallStats,
) -> V2PipelineOutput:
    started = time.time()
    segments = build_segments(ocr)
    segment_stage = {
        "stage": "segments",
        "label": "Segments",
        "input_candidates": [],
        "llm_candidates": [],
        "candidates": [segment.as_dict() for segment in segments],
        "removed": [],
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }

    mention_stage = _run_mention_scan(client, ocr, stats)
    mining_stage = _build_candidate_mining_stage(ocr, mention_stage["candidates"])
    span_stage = _build_span_gate_stage(ocr, mining_stage["candidates"])
    merge_stage = _build_merge_stage(span_stage["candidates"])
    context_stage = _build_context_stage(ocr, merge_stage["candidates"], segments)
    role_stage = _run_role_label_stage(client, context_stage["candidates"], report_type, classify_record, stats)
    deterministic_stage = _build_deterministic_signal_stage(ocr, context_stage["candidates"], role_stage["labels_by_id"])
    escalation_stage = _run_escalation_stage(
        client,
        context_stage["candidates"],
        deterministic_stage["signals_by_id"],
        role_stage["labels_by_id"],
        report_type,
        stats,
    )
    labels_by_id = dict(role_stage["labels_by_id"])
    labels_by_id.update(escalation_stage["labels_by_id"])
    decision_stage, final_people, removed, final_reasons, review_rows = _build_decision_stage(
        context_stage["candidates"],
        deterministic_stage["signals_by_id"],
        labels_by_id,
    )
    review_stage = {
        "stage": "review_queue",
        "label": "Review queue",
        "input_candidates": context_stage["candidates"],
        "llm_candidates": [],
        "candidates": review_rows,
        "removed": [],
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }

    passes = {
        "segments": segment_stage,
        "mention_scan": mention_stage,
        "candidate_mining": mining_stage,
        "span_gate": span_stage,
        "merge": merge_stage,
        "context_bundle": context_stage,
        "role_label": role_stage["stage"],
        "escalation": escalation_stage["stage"],
        "deterministic_signals": deterministic_stage["stage"],
        "decision": decision_stage,
        "review_queue": review_stage,
    }
    elapsed = round(time.time() - started, 2)
    return V2PipelineOutput(
        passes=passes,
        final_people=final_people,
        removed_candidates=[*span_stage["removed"], *merge_stage["removed"], *removed],
        final_reasons=final_reasons,
        elapsed_seconds=elapsed,
    )


def _run_mention_scan(client: Any, ocr: str, stats: CallStats) -> dict[str, Any]:
    prompt_text = load_prompt_text("name_extractor", "v2/mention_scan.txt", fallback_text=_DEFAULT_MENTION_PROMPT)
    rendered_prompt = render_prompt(prompt_text, ocr=ocr)
    obj = client.generate_json(rendered_prompt, MENTION_SCHEMA_HINT, stats, num_predict=900)
    candidates = _parse_mention_candidates(obj)
    return {
        "stage": "mention_scan",
        "label": "Mention scan",
        "input_candidates": [],
        "llm_candidates": candidates,
        "candidates": candidates,
        "removed": [],
        "prompt_name": "v2/mention_scan.txt",
        "rendered_prompt": rendered_prompt,
        "response_json": obj if isinstance(obj, dict) else {"raw": obj},
        "fallback_applied": False,
        "fallback_reason": "",
    }


def _parse_mention_candidates(obj: Any) -> list[dict[str, str]]:
    if not isinstance(obj, dict):
        return []
    rows: list[dict[str, str]] = []
    if isinstance(obj.get("names"), list):
        for item in obj["names"]:
            if not isinstance(item, dict):
                continue
            name = canonicalize_candidate_name(str(item.get("name") or ""))
            if looks_like_candidate_name(name):
                rows.append({"name": name, "evidence": clean_evidence(item.get("span_quote") or item.get("evidence") or "")})
    rows.extend(parse_named_people(obj))
    return merge_name_candidates(rows)


def _build_candidate_mining_stage(ocr: str, llm_candidates: list[dict[str, str]]) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}

    def add(name: str, evidence: str, source: str) -> None:
        canonical = canonicalize_candidate_name(name)
        if not canonical or not looks_like_candidate_name(canonical):
            return
        key = canonical.casefold()
        row = candidates.setdefault(
            key,
            {
                "candidate_id": "",
                "name": canonical,
                "evidence": clean_evidence(evidence),
                "sources": [],
                "kind": "relation_label" if looks_like_subject_label(canonical) else "person",
            },
        )
        if source not in row["sources"]:
            row["sources"].append(source)
        if len(clean_evidence(evidence)) > len(row.get("evidence", "")):
            row["evidence"] = clean_evidence(evidence)

    for item in rule_seed_candidates(ocr):
        add(item.get("name", ""), item.get("evidence", ""), "regex_seed")
    for item in llm_candidates:
        add(item.get("name", ""), item.get("evidence", ""), "llm_mention_scan")

    rows = sorted(candidates.values(), key=lambda item: item["name"].lower())
    for index, row in enumerate(rows, start=1):
        row["candidate_id"] = f"cand_{index:03d}"
    return {
        "stage": "candidate_mining",
        "label": "Candidate mining",
        "input_candidates": [dict(item) for item in llm_candidates],
        "llm_candidates": [dict(item) for item in llm_candidates],
        "candidates": rows,
        "removed": [],
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }


def _build_span_gate_stage(ocr: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for item in candidates:
        name = item.get("name", "")
        if is_generic_subject_phrase(name):
            removed.append(_removed(item, "span_gate", "generic_unanchored", "Candidate is an unnamed generic subject phrase."))
            continue
        occurrences = _find_occurrences(name, ocr)
        if not occurrences:
            removed.append(_removed(item, "span_gate", "absent_from_ocr", "Candidate name is not present on this OCR page."))
            continue
        evidence = item.get("evidence", "")
        if "llm_mention_scan" in item.get("sources", []) and evidence and not _quote_in_text(evidence, ocr):
            removed.append(_removed(item, "span_gate", "evidence_absent_from_ocr", "Model quote is not present on this OCR page."))
            continue
        updated = dict(item)
        updated["occurrences"] = occurrences
        updated["span_start"] = occurrences[0]["start"]
        updated["span_end"] = occurrences[0]["end"]
        updated["matched_text"] = occurrences[0]["text"]
        kept.append(updated)
    return {
        "stage": "span_gate",
        "label": "Span gate",
        "input_candidates": candidates,
        "llm_candidates": [],
        "candidates": kept,
        "removed": removed,
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }


def _build_merge_stage(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    clusters: list[list[dict[str, Any]]] = []
    for item in candidates:
        for cluster in clusters:
            if any(_candidates_same_merge_subject(item, other) for other in cluster):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    merged: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        preferred_name = merge_name_candidates([{"name": item["name"], "evidence": item.get("evidence", "")} for item in cluster])[0]
        preferred = max(cluster, key=lambda item: (item["name"] == preferred_name["name"], len(item["name"]), len(item.get("evidence", ""))))
        row = dict(preferred)
        row["candidate_id"] = f"cand_{index:03d}"
        row["name"] = preferred_name["name"]
        row["evidence"] = preferred_name.get("evidence") or preferred.get("evidence", "")
        row["sources"] = sorted({source for item in cluster for source in item.get("sources", [])})
        row["occurrences"] = [occ for item in cluster for occ in item.get("occurrences", [])]
        merged.append(row)
        for item in cluster:
            if item is preferred:
                continue
            removed.append(
                {
                    "name": item.get("name", ""),
                    "evidence": clean_evidence(item.get("evidence", "")),
                    "stage": "merge",
                    "reason_type": "merged_variant",
                    "reason": f'Merged into "{row["name"]}".',
                    "excerpt": clean_evidence(item.get("evidence", "")),
                    "kept_as": row["name"],
                }
            )
    merged.sort(key=lambda item: item["name"].lower())
    for index, row in enumerate(merged, start=1):
        row["candidate_id"] = f"cand_{index:03d}"
    return {
        "stage": "merge",
        "label": "Merge",
        "input_candidates": candidates,
        "llm_candidates": [],
        "candidates": merged,
        "removed": removed,
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }


def _build_context_stage(ocr: str, candidates: list[dict[str, Any]], segments: list[TextSegment]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in candidates:
        occurrence = (item.get("occurrences") or [{}])[0]
        start = int(occurrence.get("start", item.get("span_start", 0)))
        end = int(occurrence.get("end", item.get("span_end", start)))
        numbered = containing_segments(segments, start, end, "numbered_case")
        subject_list = containing_segments(segments, start, end, "subject_list_block")
        paragraph = containing_segments(segments, start, end, "paragraph")
        primary = (numbered or subject_list or paragraph or [expanded_window(ocr, start, end)])[0]
        header = nearest_segment(segments, 0, 0, "header")
        window = expanded_window(ocr, start, end)
        context_text = _bundle_text(header, primary, window)
        row = dict(item)
        row.update(
            {
                "bundle_id": f"bundle_{item['candidate_id']}",
                "bundle_type": _bundle_type(primary),
                "primary_segment_id": primary.segment_id,
                "primary_context": primary.text,
                "header_context": header.text if header else "",
                "expanded_window": window.text,
                "context": context_text,
                "context_span": [min(primary.start, window.start), max(primary.end, window.end)],
                "context_reason": _context_reason(primary),
            }
        )
        rows.append(row)
    return {
        "stage": "context_bundle",
        "label": "Context bundle",
        "input_candidates": candidates,
        "llm_candidates": [],
        "candidates": rows,
        "removed": [],
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }


def _run_role_label_stage(
    client: Any,
    candidates: list[dict[str, Any]],
    report_type: str,
    classify_record: dict[str, Any],
    stats: CallStats,
) -> dict[str, Any]:
    if not candidates:
        return {"stage": _empty_stage("role_label", "Role label"), "labels_by_id": {}}
    prompt_text = load_prompt_text("name_extractor", "v2/role_label.txt", fallback_text=_DEFAULT_ROLE_PROMPT)
    batches = _role_batches(candidates)
    labels_by_id: dict[str, dict[str, Any]] = {}
    rendered_prompts: list[str] = []
    responses: list[Any] = []
    for batch in batches:
        payload = json.dumps(_role_payload(batch, report_type, classify_record), ensure_ascii=False, indent=2)
        rendered_prompt = render_prompt(prompt_text, role_payload_json=payload)
        obj = client.generate_json(rendered_prompt, ROLE_SCHEMA_HINT, stats, num_predict=1000)
        rendered_prompts.append(rendered_prompt)
        responses.append(obj)
        for label in _parse_role_labels(obj, batch):
            labels_by_id[label["candidate_id"]] = label
    stage = {
        "stage": "role_label",
        "label": "Role label",
        "input_candidates": candidates,
        "llm_candidates": list(labels_by_id.values()),
        "candidates": list(labels_by_id.values()),
        "removed": [],
        "prompt_name": "v2/role_label.txt",
        "rendered_prompt": "\n\n--- BATCH ---\n\n".join(rendered_prompts),
        "response_json": {"batches": responses},
        "fallback_applied": False,
        "fallback_reason": "",
    }
    return {"stage": stage, "labels_by_id": labels_by_id}


def _build_deterministic_signal_stage(
    ocr: str,
    candidates: list[dict[str, Any]],
    labels_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    signals_by_id: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for item in candidates:
        signals: list[dict[str, Any]] = []
        decision = explain_candidate_decision(item.get("name", ""), item.get("evidence", ""), ocr)
        if decision["keep"]:
            signals.append({"type": "strong_positive_regex", "weight": 2.5, "reason": decision["reason"], "excerpt": decision["excerpt"]})
        elif decision["reason_type"] in {"negative_rule", "freeborn_not_slave"}:
            weight = -5.0 if decision["reason_type"] == "freeborn_not_slave" else -4.0
            signals.append({"type": f"hard_negative_regex:{decision['reason_type']}", "weight": weight, "reason": decision["reason"], "excerpt": decision["excerpt"]})
        elif decision["reason_type"] == "ambiguous_subject_role":
            signals.append({"type": "ambiguous_deterministic_role", "weight": 0.0, "reason": decision["reason"], "excerpt": decision["excerpt"]})

        if item.get("bundle_type") == "subject_list_block" and SUBJECT_ACTION_RE.search(item.get("context", "")):
            signals.append({"type": "validated_subject_list_context", "weight": 2.0, "reason": "Candidate appears inside a subject-list block.", "excerpt": clean_evidence(item.get("primary_context", ""))})
        if item.get("bundle_type") == "numbered_case" and SUBJECT_ACTION_RE.search(item.get("context", "")):
            signals.append({"type": "validated_numbered_case_context", "weight": 1.5, "reason": "Candidate appears inside a numbered case with subject action.", "excerpt": clean_evidence(item.get("primary_context", ""))})

        label = labels_by_id.get(item["candidate_id"])
        if label:
            signals.extend(_role_signals(label, item))
        signals_by_id[item["candidate_id"]] = signals
        rows.append({"candidate_id": item["candidate_id"], "name": item["name"], "signals": signals})
    return {
        "stage": {
            "stage": "deterministic_signals",
            "label": "Deterministic signals",
            "input_candidates": candidates,
            "llm_candidates": [],
            "candidates": rows,
            "removed": [],
            "prompt_name": "",
            "rendered_prompt": "",
            "response_json": {},
            "fallback_applied": False,
            "fallback_reason": "",
        },
        "signals_by_id": signals_by_id,
    }


def _run_escalation_stage(
    client: Any,
    candidates: list[dict[str, Any]],
    signals_by_id: dict[str, list[dict[str, Any]]],
    labels_by_id: dict[str, dict[str, Any]],
    report_type: str,
    stats: CallStats,
) -> dict[str, Any]:
    prompt_text = load_prompt_text("name_extractor", "v2/role_escalate.txt", fallback_text=_DEFAULT_ESCALATE_PROMPT)
    labels: dict[str, dict[str, Any]] = {}
    rendered_prompts: list[str] = []
    responses: list[Any] = []
    for item in candidates:
        current = labels_by_id.get(item["candidate_id"], {})
        if not _needs_escalation(item, current, signals_by_id.get(item["candidate_id"], [])):
            continue
        payload = json.dumps(_role_payload([item], report_type, {}), ensure_ascii=False, indent=2)
        rendered_prompt = render_prompt(prompt_text, role_payload_json=payload)
        obj = client.generate_json(rendered_prompt, ROLE_SCHEMA_HINT, stats, num_predict=900)
        rendered_prompts.append(rendered_prompt)
        responses.append(obj)
        parsed = _parse_role_labels(obj, [item])
        if parsed and parsed[0]["confidence"] in {"medium", "high"} and not parsed[0].get("role_evidence_invalid"):
            labels[item["candidate_id"]] = parsed[0]
    return {
        "stage": {
            "stage": "escalation",
            "label": "Escalation",
            "input_candidates": [item for item in candidates if item["candidate_id"] in labels],
            "llm_candidates": list(labels.values()),
            "candidates": list(labels.values()),
            "removed": [],
            "prompt_name": "v2/role_escalate.txt",
            "rendered_prompt": "\n\n--- ESCALATION ---\n\n".join(rendered_prompts),
            "response_json": {"batches": responses},
            "fallback_applied": False,
            "fallback_reason": "",
        },
        "labels_by_id": labels,
    }


def _build_decision_stage(
    candidates: list[dict[str, Any]],
    signals_by_id: dict[str, list[dict[str, Any]]],
    labels_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    final_people: list[dict[str, str]] = []
    removed: list[dict[str, Any]] = []
    final_reasons: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for item in candidates:
        signals = signals_by_id.get(item["candidate_id"], [])
        score = round(sum(float(signal.get("weight", 0.0)) for signal in signals), 2)
        has_positive = any(float(signal.get("weight", 0)) > 0 for signal in signals)
        has_hard_negative = any(str(signal.get("type", "")).startswith("hard_negative") or str(signal.get("type", "")).startswith("negative_role") for signal in signals)
        evidence = _best_evidence(item, labels_by_id.get(item["candidate_id"]), signals)
        decision = "review"
        reason_type = "ambiguous_role"
        reason = "Candidate score is ambiguous; excluded from final CSV and retained for review."
        if score >= 2.5 and not (has_hard_negative and has_positive):
            decision = "keep"
            reason_type = "kept_subject_score"
            reason = "Candidate passed V2 subject score threshold."
        elif score >= 2.5 and has_hard_negative and not has_positive:
            decision = "drop"
            reason_type = "hard_negative_role"
            reason = "Hard negative role outweighed subject evidence."
        elif score <= -1.0:
            decision = "drop"
            reason_type = "hard_negative_role" if has_hard_negative else "low_subject_score"
            reason = "Candidate did not pass V2 subject score threshold."

        row = {
            "candidate_id": item["candidate_id"],
            "name": item["name"],
            "score": score,
            "decision": decision,
            "signals": signals,
            "excerpt": evidence,
            "reason_type": reason_type,
            "reason": reason,
        }
        decision_rows.append(row)
        if decision == "keep":
            final_people.append({"name": item["name"], "evidence": evidence})
            final_reasons.append({"name": item["name"], "stage": "decision", **{key: row[key] for key in ("reason_type", "reason", "score", "signals", "excerpt")}})
        elif decision == "drop":
            removed.append({"name": item["name"], "evidence": clean_evidence(item.get("evidence", "")), "stage": "decision", **{key: row[key] for key in ("reason_type", "reason", "score", "excerpt")}})
        else:
            review_rows.append(row)
            removed.append({"name": item["name"], "evidence": clean_evidence(item.get("evidence", "")), "stage": "decision", **{key: row[key] for key in ("reason_type", "reason", "score", "excerpt")}})

    final_people = merge_name_candidates(final_people)
    stage = {
        "stage": "decision",
        "label": "Decision",
        "input_candidates": candidates,
        "llm_candidates": [],
        "candidates": decision_rows,
        "removed": removed,
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }
    return stage, final_people, removed, final_reasons, review_rows


def _role_signals(label: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    role = label.get("role", "ambiguous")
    confidence = label.get("confidence", "low")
    evidence = clean_evidence(label.get("evidence_quote", ""))
    if label.get("role_evidence_invalid"):
        invalid = [{"type": "invalid_role_evidence", "weight": -2.0, "reason": "Role evidence quote did not validate against context bundle.", "excerpt": evidence}]
    else:
        invalid = []
    if role in POSITIVE_ROLES:
        weight = CONFIDENCE_WEIGHT.get(confidence, 1.0)
        if role == "relation_subject" or looks_like_subject_label(item.get("name", "")):
            weight = max(weight, 2.0)
        return [*invalid, {"type": f"role_subject_{confidence}", "weight": weight, "role": role, "reason": f"Mistral labeled candidate as {role}.", "excerpt": evidence}]
    if role in NEGATIVE_ROLES:
        return [*invalid, {"type": f"negative_role:{role}", "weight": -3.0, "role": role, "reason": f"Mistral labeled candidate as {role}.", "excerpt": evidence}]
    return [*invalid, {"type": "ambiguous_role", "weight": 0.0, "role": role, "reason": "Mistral role label was ambiguous.", "excerpt": evidence}]


def _parse_role_labels(obj: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    by_id = {item["candidate_id"]: item for item in candidates}
    labels = obj.get("labels") or obj.get("role_labels") or []
    parsed: list[dict[str, Any]] = []
    for label in labels:
        if not isinstance(label, dict):
            continue
        candidate_id = str(label.get("candidate_id") or "")
        if candidate_id not in by_id:
            continue
        role = str(label.get("role") or "ambiguous").strip().lower()
        if role not in ALLOWED_ROLES:
            role = "ambiguous"
        confidence = str(label.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        evidence = clean_evidence(label.get("evidence_quote") or label.get("evidence") or "")
        context = by_id[candidate_id].get("context", "")
        parsed.append(
            {
                "candidate_id": candidate_id,
                "name": by_id[candidate_id]["name"],
                "role": role,
                "confidence": confidence,
                "evidence_quote": evidence,
                "role_evidence_invalid": bool(evidence and not _quote_in_text(evidence, context)),
            }
        )
    return parsed


def _find_occurrences(name: str, ocr: str) -> list[dict[str, Any]]:
    pattern = build_name_regex(name)
    if not pattern:
        return []
    return [{"start": match.start(), "end": match.end(), "text": match.group(0)} for match in pattern.finditer(ocr or "")]


def _quote_in_text(quote: str, text: str) -> bool:
    cleaned_quote = normalize_ws(quote).casefold()
    cleaned_text = normalize_ws(text).casefold()
    return bool(cleaned_quote and cleaned_quote in cleaned_text)


def _candidates_same_merge_subject(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_label = looks_like_subject_label(a.get("name", ""))
    b_label = looks_like_subject_label(b.get("name", ""))
    if a_label or b_label:
        return a_label and b_label and a.get("name", "").casefold() == b.get("name", "").casefold()
    if not names_maybe_same_person(a.get("name", ""), b.get("name", "")):
        return False
    return _spans_near(a.get("occurrences", []), b.get("occurrences", []))


def _spans_near(a_occ: list[dict[str, Any]], b_occ: list[dict[str, Any]], *, distance: int = 100) -> bool:
    for a in a_occ:
        for b in b_occ:
            if abs(int(a.get("start", 0)) - int(b.get("start", 0))) <= distance:
                return True
    return False


def _bundle_type(segment: TextSegment) -> str:
    return segment.type


def _context_reason(segment: TextSegment) -> str:
    if segment.type == "numbered_case":
        return "candidate appears inside a numbered case segment"
    if segment.type == "subject_list_block":
        return "candidate appears inside a subject-list block"
    if segment.type == "paragraph":
        return "candidate appears inside a paragraph context"
    return "candidate uses expanded local context"


def _bundle_text(header: TextSegment | None, primary: TextSegment, window: TextSegment) -> str:
    parts = []
    if header and header.text != primary.text:
        parts.append(f"PAGE HEADER: {header.text}")
    parts.append(f"PRIMARY CONTEXT: {primary.text}")
    if window.text not in primary.text:
        parts.append(f"LOCAL WINDOW: {window.text}")
    return normalize_ws("\n".join(parts))


def _role_batches(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        grouped[str(item.get("primary_segment_id") or item["candidate_id"])].append(item)
    batches: list[list[dict[str, Any]]] = []
    for rows in grouped.values():
        for index in range(0, len(rows), 6):
            batches.append(rows[index : index + 6])
    return batches


def _role_payload(batch: list[dict[str, Any]], report_type: str, classify_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "classifier_evidence": classify_record.get("evidence", ""),
        "context": batch[0].get("context", "") if batch else "",
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "name": item["name"],
                "kind": item.get("kind", "person"),
                "context_reason": item.get("context_reason", ""),
            }
            for item in batch
        ],
    }


def _needs_escalation(item: dict[str, Any], label: dict[str, Any], signals: list[dict[str, Any]]) -> bool:
    if not label:
        return False
    if label.get("role") == "ambiguous" or label.get("confidence") == "low" or label.get("role_evidence_invalid"):
        return True
    has_positive = any(float(signal.get("weight", 0)) > 0 for signal in signals)
    has_negative = any(float(signal.get("weight", 0)) < 0 for signal in signals)
    if has_positive and has_negative:
        return True
    if looks_like_subject_label(item.get("name", "")) and label.get("role") != "relation_subject":
        return True
    return False


def _best_evidence(item: dict[str, Any], label: dict[str, Any] | None, signals: list[dict[str, Any]]) -> str:
    if label and label.get("evidence_quote") and not label.get("role_evidence_invalid"):
        return clean_evidence(label["evidence_quote"])
    for signal in signals:
        if signal.get("excerpt"):
            return clean_evidence(signal["excerpt"])
    return clean_evidence(item.get("evidence") or item.get("primary_context") or item.get("context") or "")


def _removed(item: dict[str, Any], stage: str, reason_type: str, reason: str) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "evidence": clean_evidence(item.get("evidence", "")),
        "stage": stage,
        "reason_type": reason_type,
        "reason": reason,
        "excerpt": clean_evidence(item.get("evidence", "")),
    }


def _empty_stage(stage: str, label: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "label": label,
        "input_candidates": [],
        "llm_candidates": [],
        "candidates": [],
        "removed": [],
        "prompt_name": "",
        "rendered_prompt": "",
        "response_json": {},
        "fallback_applied": False,
        "fallback_reason": "",
    }


_DEFAULT_MENTION_PROMPT = """List every visible personal name or visible relation-labeled subject phrase in this OCR page.
Do not decide whether the person is a subject. Do not invent names. Return JSON only:
{"names":[{"name":"...","span_quote":"short verbatim quote containing the name"}]}

OCR TEXT:
<<<{ocr}>>>
"""

_DEFAULT_ROLE_PROMPT = """Classify the role of each provided candidate using only the provided context.
Do not add names. Use only the allowed role labels. Return JSON only:
{"labels":[{"candidate_id":"cand_001","role":"ambiguous","confidence":"low","evidence_quote":"verbatim quote from context"}]}

Allowed positive roles: enslaved_subject, refugee_slave, fugitive_slave, manumission_applicant, certificate_recipient, kidnapped_victim, recovered_person, repatriated_person, slave_status_investigation_subject, relation_subject.
Allowed negative roles: owner, master, buyer, seller, broker, kidnapper, witness, official, correspondent, signatory, papers_source, family_member_only, freeborn_not_slave, generic_unanchored.
Neutral role: ambiguous.

PAYLOAD:
<<<{role_payload_json}>>>
"""

_DEFAULT_ESCALATE_PROMPT = """Re-check this hard case using the same role labels. Use only the provided candidate IDs and context.
Return JSON only:
{"labels":[{"candidate_id":"cand_001","role":"ambiguous","confidence":"low","evidence_quote":"verbatim quote from context"}]}

PAYLOAD:
<<<{role_payload_json}>>>
"""
