"""Normalization utilities used by extraction and aggregation modules."""

from .dates import extract_doc_year, parse_first_date_in_text, to_iso_date
from .evidence import clean_evidence, normalize_for_match
from .names import (
    build_name_regex,
    choose_preferred_name,
    is_valid_name,
    merge_named_people,
    name_compare_tokens,
    names_maybe_same_person,
    normalize_name,
)
from .places import PLACE_MAP, dedupe_place_rows, is_valid_place, normalize_place

__all__ = [
    "PLACE_MAP",
    "build_name_regex",
    "choose_preferred_name",
    "clean_evidence",
    "dedupe_place_rows",
    "extract_doc_year",
    "is_valid_name",
    "is_valid_place",
    "merge_named_people",
    "name_compare_tokens",
    "names_maybe_same_person",
    "normalize_for_match",
    "normalize_name",
    "normalize_place",
    "parse_first_date_in_text",
    "to_iso_date",
]
