from __future__ import annotations

import unittest

from shared.schemas import DETAIL_COLUMNS, PLACE_COLUMNS, STATUS_COLUMNS, DetailRow, PageDecision, PlaceRow


class SchemaTests(unittest.TestCase):
    def test_page_decision_defaults(self) -> None:
        decision = PageDecision(should_extract=True)
        self.assertEqual(decision.report_type, "statement")
        self.assertIsNone(decision.skip_reason)

    def test_detail_and_place_rows(self) -> None:
        detail = DetailRow(name="A", page=1, report_type="statement")
        place = PlaceRow(name="A", page=1, place="Zanzibar")
        self.assertEqual(detail.amount_paid, "")
        self.assertEqual(place.order, 0)

    def test_column_constants(self) -> None:
        self.assertIn("Name", DETAIL_COLUMNS)
        self.assertIn("Place", PLACE_COLUMNS)
        self.assertIn("model_calls", STATUS_COLUMNS)


if __name__ == "__main__":
    unittest.main()

