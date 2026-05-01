from __future__ import annotations

import unittest

from modules.place_extractor.parsing import parse_candidate_places, parse_place_rows


class ParsingTests(unittest.TestCase):
    def test_parse_candidate_places_filters_invalid_place_text(self) -> None:
        rows = parse_candidate_places(
            {
                "places": [
                    {"place": "H.M.S. Shoreham", "time_text": None, "evidence": "H.M.S. Shoreham transported the party"},
                    {"place": "shargah", "time_text": "May 1931", "evidence": "arrived at shargah in May 1931"},
                ]
            },
            "Mariam bint Yusuf",
            1,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Place"], "Sharjah")
        self.assertEqual(rows[0]["Time Info"], "May 1931")

    def test_parse_place_rows_derives_date_from_doc_year(self) -> None:
        rows = parse_place_rows(
            {
                "places": [
                    {
                        "place": "Dubai",
                        "order": 2,
                        "arrival_date": "17th May",
                        "date_confidence": "derived_from_doc",
                        "time_text": "",
                        "evidence": "arrived at Dubai about the 17th May",
                    },
                    {
                        "place": "Political Agency",
                        "order": 0,
                        "arrival_date": None,
                        "date_confidence": "unknown",
                        "time_text": None,
                        "evidence": "Political Agency",
                    },
                ]
            },
            "Mariam bint Yusuf",
            1,
            1931,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Place"], "Dubai")
        self.assertEqual(rows[0]["Arrival Date"], "1931-05-17")
        self.assertEqual(rows[0]["Date Confidence"], "derived_from_doc")

    def test_parse_place_rows_rejects_prose_clause_as_place(self) -> None:
        rows = parse_place_rows(
            {
                "places": [
                    {
                        "place": "Without The Slightest Pressure On Either",
                        "order": 1,
                        "arrival_date": None,
                        "date_confidence": "unknown",
                        "time_text": "",
                        "evidence": "arrived at without the slightest pressure on either side",
                    }
                ]
            },
            "Abdulla, son of",
            10,
            1907,
        )
        self.assertEqual(rows, [])

    def test_parse_place_rows_clears_admin_and_planned_dates(self) -> None:
        rows = parse_place_rows(
            {
                "places": [
                    {
                        "place": "Koweit",
                        "order": 2,
                        "arrival_date": "1907-06-10",
                        "date_confidence": "explicit",
                        "time_text": "until the Autumn, when he would sail",
                        "evidence": "Copy of a letter No. 282, dated the 10th June 1907, from the Political Agent, Koweit.",
                    },
                    {
                        "place": "Bushehr",
                        "order": 3,
                        "arrival_date": "1907-06-10",
                        "date_confidence": "derived_from_doc",
                        "time_text": "to-day",
                        "evidence": "Abdulla will be conveyed to-day to H.M.S. Lapwing for passage to Bushire.",
                    },
                ]
            },
            "Abdulla, son of",
            10,
            1907,
        )
        self.assertEqual([(row["Place"], row["Arrival Date"], row["Date Confidence"]) for row in rows], [("Koweit", "", ""), ("Bushehr", "", "")])


if __name__ == "__main__":
    unittest.main()
