from __future__ import annotations

import unittest

from modules.normalizer.places import PLACE_MAP, dedupe_place_rows, is_valid_place, normalize_place


class PlaceTests(unittest.TestCase):
    def test_place_map_loaded_from_yaml_and_defaults(self) -> None:
        self.assertEqual(normalize_place("shargah"), "Sharjah")
        self.assertEqual(normalize_place("busheir"), "Bushehr")
        self.assertIn("shargah", PLACE_MAP)

    def test_normalize_place_connectors(self) -> None:
        self.assertEqual(normalize_place("ras ul khaimah"), "Ras al Khaimah")

    def test_rejects_ship_names_and_generic_words(self) -> None:
        self.assertFalse(is_valid_place("H.M.S. Lawrence"))
        self.assertFalse(is_valid_place("agency"))
        self.assertFalse(is_valid_place("Without The Slightest Pressure On Either"))

    def test_dedupe_place_rows_merges_dates_and_reorders(self) -> None:
        rows = dedupe_place_rows(
            [
                {"Name": "Mariam", "Place": "shargah", "Order": 2},
                {"Name": "Mariam", "Place": "Sharjah", "Order": 1, "Arrival Date": "1931-05-17", "Date Confidence": "explicit"},
                {"Name": "Mariam", "Place": "Dubai", "Order": 2},
            ]
        )
        self.assertEqual([row["Place"] for row in rows], ["Sharjah", "Dubai"])
        self.assertEqual(rows[0]["Order"], 1)
        self.assertEqual(rows[0]["Arrival Date"], "1931-05-17")


if __name__ == "__main__":
    unittest.main()
