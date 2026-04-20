from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


def build_stats(
    detail_rows: list[dict[str, Any]],
    place_rows: list[dict[str, Any]],
    status_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    pages = len(status_rows)
    skipped = sum(1 for row in status_rows if str(row.get("status", "")).startswith("skip:"))
    return {
        "pages_processed": pages,
        "skip_rate": round(skipped / pages, 3) if pages else 0,
        "unique_people": len({str(row.get("Name") or "").lower() for row in detail_rows if row.get("Name")}),
        "unique_places": len({str(row.get("Place") or "").lower() for row in place_rows if row.get("Place")}),
        "detail_rows": len(detail_rows),
        "place_rows": len(place_rows),
        "status_rows": len(status_rows),
        "report_types": dict(Counter(str(row.get("Report Type") or "") for row in detail_rows if row.get("Report Type"))),
        "crime_types": dict(Counter(str(row.get("Crime Type") or "") for row in detail_rows if row.get("Crime Type"))),
        "statuses": dict(Counter(str(row.get("status") or "") for row in status_rows if row.get("status"))),
    }


def read_csv_preview(path: Path, *, limit: int = 100) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[:limit]
