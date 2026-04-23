from __future__ import annotations

import unittest

from modules.place_extractor.validation import validation_report, verify_place_rows_need_retry


class ValidationTests(unittest.TestCase):
    def test_verify_requires_consecutive_positive_orders(self) -> None:
        rows = [
            {"Place": "Mekran", "Order": 1, "Arrival Date": "", "Date Confidence": "", "Time Info": ""},
            {"Place": "Dubai", "Order": 3, "Arrival Date": "1931-05-17", "Date Confidence": "explicit", "Time Info": ""},
        ]
        self.assertEqual(verify_place_rows_need_retry(rows), "Positive orders must be consecutive 1..n.")

    def test_verify_rejects_duplicate_places_and_bad_confidence(self) -> None:
        rows = [
            {"Place": "Dubai", "Order": 1, "Arrival Date": "", "Date Confidence": "explicit", "Time Info": ""},
            {"Place": "Dubai", "Order": 2, "Arrival Date": "1931-05-17", "Date Confidence": "explicit", "Time Info": ""},
        ]
        report = validation_report(rows)
        self.assertEqual(report[1]["status"], "fail")
        self.assertEqual(report[2]["status"], "fail")
        self.assertEqual(verify_place_rows_need_retry(rows), "Duplicate final places remain: Dubai.")

    def test_verify_accepts_sorted_unique_rows(self) -> None:
        rows = [
            {"Place": "Mekran", "Order": 1, "Arrival Date": "", "Date Confidence": "", "Time Info": ""},
            {"Place": "Dubai", "Order": 2, "Arrival Date": "1931-05-17", "Date Confidence": "explicit", "Time Info": ""},
            {"Place": "Zanzibar", "Order": 0, "Arrival Date": "", "Date Confidence": "", "Time Info": ""},
        ]
        self.assertIsNone(verify_place_rows_need_retry(rows))
        self.assertTrue(all(item["status"] == "ok" for item in validation_report(rows)))


if __name__ == "__main__":
    unittest.main()
