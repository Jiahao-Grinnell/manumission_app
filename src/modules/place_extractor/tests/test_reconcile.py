from __future__ import annotations

import unittest
from pathlib import Path

from modules.place_extractor.reconcile import infer_forwarding_transport_rows, reconcile_place_rows


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ReconcileTests(unittest.TestCase):
    def test_infer_forwarding_transport_rows_extracts_source_and_destination(self) -> None:
        rows = infer_forwarding_transport_rows("Mariam bint Yusuf", _fixture("multi_route.txt"), 1, 1931)
        self.assertEqual([row["Place"] for row in rows], ["Bushehr", "Dubai"])
        self.assertEqual(rows[1]["Arrival Date"], "1931-05-17")
        self.assertEqual(rows[1]["Date Confidence"], "explicit")

    def test_infer_forwarding_transport_rows_ignores_non_place_arrived_phrase(self) -> None:
        rows = infer_forwarding_transport_rows("Abdulla, son of", _fixture("abdulla_p010.txt"), 10, 1907)
        self.assertEqual(rows, [])

    def test_reconcile_promotes_confident_rows_and_keeps_background(self) -> None:
        ocr = (
            _fixture("single_place.txt")
            + "\nShe was taken to Mekran for about five years.\n"
            + "Arrived at Dubai about the 17th May 1931.\n"
        )
        rows = [
            {"Name": "Mariam bint Yusuf", "Page": 1, "Place": "Zanzibar", "Order": 0, "Arrival Date": "", "Date Confidence": "", "Time Info": "", "_evidence": "native of Zanzibar"},
            {"Name": "Mariam bint Yusuf", "Page": 1, "Place": "Mekran", "Order": 0, "Arrival Date": "", "Date Confidence": "", "Time Info": "for about five years", "_evidence": "taken to Mekran for about five years"},
            {"Name": "Mariam bint Yusuf", "Page": 1, "Place": "Dubai", "Order": 0, "Arrival Date": "1931-05-17", "Date Confidence": "explicit", "Time Info": "17th May 1931", "_evidence": "Arrived at Dubai about the 17th May 1931"},
        ]
        reconciled = reconcile_place_rows(rows, ocr, "Mariam bint Yusuf", 1, 1931)
        route = [row for row in reconciled if row["Order"] > 0]
        background = [row for row in reconciled if row["Order"] == 0]
        self.assertEqual([row["Place"] for row in route], ["Mekran", "Dubai"])
        self.assertEqual(background[0]["Place"], "Zanzibar")
        self.assertTrue(all("_position" not in row for row in reconciled))

    def test_reconcile_preserves_existing_positive_route_without_reordering(self) -> None:
        rows = [
            {"Name": "Faraj bin Said", "Page": 6, "Place": "Bahrain", "Order": 1, "Arrival Date": "", "Date Confidence": "", "Time Info": "before 1907-05-19", "_evidence": "Held a manumission certificate from the Political Agent in Bahrain dated 19th May 1907."},
            {"Name": "Faraj bin Said", "Page": 6, "Place": "Kuwait", "Order": 2, "Arrival Date": "1907-10-27", "Date Confidence": "explicit", "Time Info": "", "_evidence": "Appeared before the author on the 27th ultimo (October 1907)."},
            {"Name": "Faraj bin Said", "Page": 6, "Place": "Bushehr", "Order": 3, "Arrival Date": "", "Date Confidence": "", "Time Info": "after 1907-11-02", "_evidence": "The case was to be put up to the Resident in Bushire for orders."},
        ]
        reconciled = reconcile_place_rows(rows, _fixture("faraj_p006.txt"), "Faraj bin Said", 6, 1907)
        self.assertEqual(
            [(row["Place"], row["Order"], row["Arrival Date"], row["Time Info"]) for row in reconciled],
            [
                ("Bahrain", 1, "", "before 1907-05-19"),
                ("Kuwait", 2, "1907-10-27", ""),
                ("Bushehr", 3, "", "after 1907-11-02"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
