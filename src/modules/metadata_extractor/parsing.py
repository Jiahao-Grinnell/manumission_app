from __future__ import annotations

from typing import Any

from shared.text_utils import normalize_ws

from .vocab import CONFLICT_TYPES, CRIME_TYPES, DETAIL_REPORT_TYPES, TRIAL_TYPES, WHETHER_ABUSE_VALUES


FIELD_LABELS = {
    "report_type": "Report Type",
    "crime_type": "Crime Type",
    "whether_abuse": "Whether abuse",
    "conflict_type": "Conflict Type",
    "trial": "Trial",
    "amount_paid": "Amount paid",
}

ROW_FIELD_MAP = {
    "report_type": "Report Type",
    "crime_type": "Crime Type",
    "whether_abuse": "Whether abuse",
    "conflict_type": "Conflict Type",
    "trial": "Trial",
    "amount_paid": "Amount paid",
}

DETAIL_FIELD_ORDER = ["report_type", "crime_type", "whether_abuse", "conflict_type", "trial", "amount_paid"]
EVIDENCE_FIELDS = ["crime_type", "whether_abuse", "conflict_type", "trial", "amount_paid"]


def clean_evidence(value: Any, *, word_limit: int = 25) -> str:
    text = normalize_ws(str(value or ""))
    if not text:
        return ""
    return " ".join(text.split()[:word_limit])


def choose_allowed(value: Any, allowed: list[str]) -> str:
    raw = normalize_ws(str(value or ""))
    if not raw:
        return ""
    lookup = {item.casefold(): item for item in allowed}
    return lookup.get(raw.casefold(), "")


def choose_yes_no_blank(value: Any) -> str:
    raw = normalize_ws(str(value or "")).casefold()
    if raw in {item.casefold() for item in WHETHER_ABUSE_VALUES}:
        return raw
    return ""


def parse_meta(
    obj: Any,
    name: str,
    page: int,
    report_type: str,
    *,
    classify_evidence: str = "",
) -> dict[str, Any]:
    row = {
        "Name": name,
        "Page": page,
        "Report Type": choose_allowed(report_type, DETAIL_REPORT_TYPES) or DETAIL_REPORT_TYPES[0],
        "Crime Type": "",
        "Whether abuse": "",
        "Conflict Type": "",
        "Trial": "",
        "Amount paid": "",
    }
    evidence_map = {field: "" for field in DETAIL_FIELD_ORDER}
    evidence_map["report_type"] = clean_evidence(classify_evidence)
    validation = {field: _empty_validation(field) for field in DETAIL_FIELD_ORDER}
    raw_values = {field: "" for field in DETAIL_FIELD_ORDER}

    if not isinstance(obj, dict):
        row["_evidence"] = evidence_map
        return {"row": row, "validation": validation, "raw_values": raw_values}

    evidence_obj = obj.get("evidence")
    evidence_source = evidence_obj if isinstance(evidence_obj, dict) else {}

    raw_values["report_type"] = normalize_ws(str(obj.get("report_type") or ""))
    model_report_type = choose_allowed(obj.get("report_type"), DETAIL_REPORT_TYPES)
    upstream_report_type = choose_allowed(report_type, DETAIL_REPORT_TYPES) or DETAIL_REPORT_TYPES[0]
    if model_report_type:
        row["Report Type"] = model_report_type
        validation["report_type"] = _validation(
            "ok",
            "Report Type is in the allowed set.",
            input_value=raw_values["report_type"],
            final_value=model_report_type,
            evidence=evidence_map["report_type"],
        )
    elif raw_values["report_type"]:
        row["Report Type"] = upstream_report_type
        validation["report_type"] = _validation(
            "inherited",
            f'Cleared invalid Report Type value "{raw_values["report_type"]}" and reused page-classifier context.',
            input_value=raw_values["report_type"],
            final_value=upstream_report_type,
            evidence=evidence_map["report_type"],
        )
    else:
        row["Report Type"] = upstream_report_type
        validation["report_type"] = _validation(
            "inherited",
            "Used page-classifier report type context.",
            final_value=upstream_report_type,
            evidence=evidence_map["report_type"],
        )

    row["Crime Type"], evidence_map["crime_type"], validation["crime_type"], raw_values["crime_type"] = _validate_choice(
        obj.get("crime_type"),
        evidence_source.get("crime_type"),
        CRIME_TYPES,
        "crime_type",
    )
    row["Whether abuse"], evidence_map["whether_abuse"], validation["whether_abuse"], raw_values["whether_abuse"] = _validate_yes_no(
        obj.get("whether_abuse"),
        evidence_source.get("whether_abuse"),
    )
    row["Conflict Type"], evidence_map["conflict_type"], validation["conflict_type"], raw_values["conflict_type"] = _validate_choice(
        obj.get("conflict_type"),
        evidence_source.get("conflict_type"),
        CONFLICT_TYPES,
        "conflict_type",
    )
    row["Trial"], evidence_map["trial"], validation["trial"], raw_values["trial"] = _validate_choice(
        obj.get("trial"),
        evidence_source.get("trial"),
        TRIAL_TYPES,
        "trial",
    )
    row["Amount paid"], evidence_map["amount_paid"], validation["amount_paid"], raw_values["amount_paid"] = _validate_amount(
        obj.get("amount_paid"),
        evidence_source.get("amount_paid"),
    )

    row["_evidence"] = evidence_map
    return {"row": row, "validation": validation, "raw_values": raw_values}


def _validate_choice(raw_value: Any, raw_evidence: Any, allowed: list[str], field: str) -> tuple[str, str, dict[str, Any], str]:
    raw_text = normalize_ws(str(raw_value or ""))
    evidence = clean_evidence(raw_evidence)
    final_value = choose_allowed(raw_value, allowed)
    if final_value and evidence:
        return (
            final_value,
            evidence,
            _validation("ok", f"{FIELD_LABELS[field]} is in the allowed set.", input_value=raw_text, final_value=final_value, evidence=evidence),
            raw_text,
        )
    if final_value and not evidence:
        return (
            "",
            "",
            _validation(
                "cleared_missing_evidence",
                f"Cleared {FIELD_LABELS[field]} because no supporting evidence was provided.",
                input_value=raw_text,
            ),
            raw_text,
        )
    if raw_text:
        return (
            "",
            "",
            _validation(
                "cleared_invalid",
                f'Cleared invalid {FIELD_LABELS[field]} value "{raw_text}".',
                input_value=raw_text,
            ),
            raw_text,
        )
    return "", "", _empty_validation(field), raw_text


def _validate_yes_no(raw_value: Any, raw_evidence: Any) -> tuple[str, str, dict[str, Any], str]:
    raw_text = normalize_ws(str(raw_value or ""))
    evidence = clean_evidence(raw_evidence)
    final_value = choose_yes_no_blank(raw_value)
    if final_value and evidence:
        return final_value, evidence, _validation("ok", "Whether abuse is one of yes/no and has evidence.", input_value=raw_text, final_value=final_value, evidence=evidence), raw_text
    if final_value and not evidence:
        return "", "", _validation("cleared_missing_evidence", "Cleared Whether abuse because no supporting evidence was provided.", input_value=raw_text), raw_text
    if raw_text:
        return "", "", _validation("cleared_invalid", f'Cleared invalid Whether abuse value "{raw_text}".', input_value=raw_text), raw_text
    return "", "", _empty_validation("whether_abuse"), raw_text


def _validate_amount(raw_value: Any, raw_evidence: Any) -> tuple[str, str, dict[str, Any], str]:
    raw_text = normalize_ws(str(raw_value or ""))
    if raw_text.casefold() in {"null", "none"}:
        raw_text = ""
    evidence = clean_evidence(raw_evidence)
    if raw_text and evidence:
        return raw_text, evidence, _validation("ok", "Amount paid kept as literal page text.", input_value=raw_text, final_value=raw_text, evidence=evidence), raw_text
    if raw_text and not evidence:
        return "", "", _validation("cleared_missing_evidence", "Cleared Amount paid because no supporting evidence was provided.", input_value=raw_text), raw_text
    return "", "", _empty_validation("amount_paid"), raw_text


def _empty_validation(field: str) -> dict[str, Any]:
    return _validation("empty", f"{FIELD_LABELS[field]} empty.")


def _validation(
    status: str,
    message: str,
    *,
    input_value: str = "",
    final_value: str = "",
    evidence: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "input_value": input_value,
        "final_value": final_value,
        "evidence": evidence,
    }

