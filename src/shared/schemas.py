from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PageDecision(BaseModel):
    should_extract: bool
    skip_reason: Literal["index", "record_metadata", "bad_ocr"] | None = None
    report_type: Literal["statement", "transport/admin", "correspondence"] = "statement"
    evidence: str = ""


class NamedPerson(BaseModel):
    name: str
    evidence: str = ""


class DetailRow(BaseModel):
    name: str
    page: int
    report_type: str
    crime_type: str = ""
    whether_abuse: str = ""
    conflict_type: str = ""
    trial: str = ""
    amount_paid: str = ""


class PlaceRow(BaseModel):
    name: str
    page: int
    place: str
    order: int = 0
    arrival_date: str = ""
    date_confidence: str = ""
    time_info: str = ""
    evidence: str = ""


class CallStats(BaseModel):
    model_calls: int = 0
    repair_calls: int = 0


DETAIL_COLUMNS = [
    "Name",
    "Page",
    "Report Type",
    "Crime Type",
    "Whether abuse",
    "Conflict Type",
    "Trial",
    "Amount paid",
]

PLACE_COLUMNS = [
    "Name",
    "Page",
    "Place",
    "Order",
    "Arrival Date",
    "Date Confidence",
    "Time Info",
]

STATUS_COLUMNS = [
    "page",
    "filename",
    "status",
    "named_people",
    "detail_rows",
    "place_rows",
    "model_calls",
    "repair_calls",
    "elapsed_seconds",
    "note",
]
