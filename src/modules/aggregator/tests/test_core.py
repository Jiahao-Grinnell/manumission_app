from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from modules.aggregator.core import aggregate


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


class AggregatorCoreTests(unittest.TestCase):
    def test_aggregate_small_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inter = root / "intermediate" / "demo"
            out = root / "output" / "demo"
            _write_json(inter / "p001.classify.json", {"should_extract": True, "report_type": "statement"})
            _write_json(inter / "p001.names.json", {"named_people": [{"name": "Mariam bint Yusuf"}]})
            _write_json(
                inter / "p001.meta.json",
                {"rows": [{"Name": "Mariam bint Yusuf", "Page": 1, "Report Type": "statement", "Crime Type": "kidnapping"}]},
            )
            _write_json(
                inter / "p001.places.json",
                {"people": [{"name": "Marium bint Yousuf", "rows": [{"Place": "shargah", "Order": 1}]}]},
            )
            _write_json(inter / "p002.classify.json", {"should_extract": False, "skip_reason": "index"})

            result = aggregate("demo", inter_dir=inter, out_dir=out)

            self.assertEqual(result.stats["detail_rows"], 1)
            self.assertEqual(result.stats["place_rows"], 1)
            self.assertEqual(result.stats["status_rows"], 2)
            self.assertTrue(result.cleanup_actions)
            self.assertTrue((out / "aggregation_summary.json").exists())
            detail_rows = _read_csv(out / "Detailed info.csv")
            place_rows = _read_csv(out / "name place.csv")
            status_rows = _read_csv(out / "run_status.csv")
            self.assertEqual(detail_rows[0]["Name"], "Mariam bint Yusuf")
            self.assertEqual(place_rows[0]["Name"], "Mariam bint Yusuf")
            self.assertEqual(place_rows[0]["Place"], "Sharjah")
            self.assertEqual(status_rows[1]["status"], "skip:index")

    def test_empty_doc_writes_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inter = root / "intermediate" / "empty"
            out = root / "output" / "empty"
            inter.mkdir(parents=True)
            result = aggregate("empty", inter_dir=inter, out_dir=out)
            self.assertEqual(result.stats["detail_rows"], 0)
            for filename in ("Detailed info.csv", "name place.csv", "run_status.csv"):
                self.assertTrue((out / filename).exists())
                self.assertGreater((out / filename).read_text(encoding="utf-8").count("\n"), 0)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


if __name__ == "__main__":
    unittest.main()
