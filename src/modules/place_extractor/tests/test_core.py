from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from modules.place_extractor.core import run_folder, run_page_file


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.waited = False

    def generate_json(self, prompt: str, schema_hint: str, stats, *, num_predict: int | None = None):  # noqa: ANN001
        stats.model_calls += 1
        if not self.responses:
            raise AssertionError("No fake response configured")
        return self.responses.pop(0)

    def wait_ready(self, timeout_s: int = 240) -> None:
        self.waited = True


class CoreTests(unittest.TestCase):
    def test_run_page_file_extracts_all_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p001.txt"
            classify = root / "p001.classify.json"
            names = root / "p001.names.json"
            target = root / "p001.places.json"
            ocr.write_text(_fixture("multi_route.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}), encoding="utf-8")
            names.write_text(
                json.dumps(
                    {
                        "page": 1,
                        "named_people": [
                            {"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"},
                            {"name": "Fatima bint Ali", "evidence": "Fatima bint Ali"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    {"places": [{"place": "Zanzibar", "time_text": None, "evidence": "native of Zanzibar"}, {"place": "Mekran", "time_text": "for about five years", "evidence": "taken to Mekran for about five years"}]},
                    {"places": [{"place": "Dubai", "order": 2, "arrival_date": "17th May 1931", "date_confidence": "explicit", "time_text": "17th May 1931", "evidence": "arriving Dubai about the 17th May 1931"}]},
                    {"places": [{"place": "Mekran", "order": 1, "arrival_date": None, "date_confidence": "", "time_text": "for about five years", "evidence": "taken to Mekran for about five years"}, {"place": "Dubai", "order": 2, "arrival_date": "1931-05-17", "date_confidence": "explicit", "time_text": "17th May 1931", "evidence": "arriving Dubai about the 17th May 1931"}]},
                    {"places": [{"place": "Mekran", "order": 1, "arrival_date": None, "date_confidence": "", "time_text": "for about five years", "evidence": "taken to Mekran for about five years"}, {"place": "Dubai", "order": 2, "arrival_date": "1931-05-17", "date_confidence": "explicit", "time_text": "17th May 1931", "evidence": "arriving Dubai about the 17th May 1931"}]},
                    {"places": []},
                    {"places": []},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client)
            self.assertEqual(len(result.people), 2)
            mariam = next(person for person in result.people if person["name"] == "Mariam bint Yusuf")
            fatima = next(person for person in result.people if person["name"] == "Fatima bint Ali")
            self.assertEqual([row["Place"] for row in mariam["rows"] if row["Order"] > 0], ["Mekran", "Dubai"])
            self.assertEqual(fatima["rows"], [])
            self.assertEqual(result.model_calls, 6)
            self.assertTrue(target.exists())

    def test_run_page_file_upserts_single_person_without_dropping_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p001.txt"
            classify = root / "p001.classify.json"
            names = root / "p001.names.json"
            target = root / "p001.places.json"
            ocr.write_text(_fixture("single_place.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}), encoding="utf-8")
            names.write_text(
                json.dumps(
                    {
                        "page": 1,
                        "named_people": [
                            {"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"},
                            {"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            target.write_text(
                json.dumps(
                    {
                        "page": 1,
                        "report_type": "statement",
                        "classify": {"report_type": "statement"},
                        "names": ["Mariam bint Yusuf", "Ahmad bin Said"],
                        "people": [
                            {
                                "name": "Mariam bint Yusuf",
                                "rows": [{"Name": "Mariam bint Yusuf", "Page": 1, "Place": "Zanzibar", "Order": 0, "Arrival Date": "", "Date Confidence": "", "Time Info": "", "_evidence": "native of Zanzibar"}],
                                "passes": {},
                                "validation": [],
                                "model_calls": 2,
                                "repair_calls": 0,
                                "elapsed_seconds": 1.0,
                            }
                        ],
                        "rows": [{"Name": "Mariam bint Yusuf", "Page": 1, "Place": "Zanzibar", "Order": 0, "Arrival Date": "", "Date Confidence": "", "Time Info": "", "_evidence": "native of Zanzibar"}],
                        "model_calls": 2,
                        "repair_calls": 0,
                        "elapsed_seconds": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    {"places": [{"place": "Dubai", "time_text": None, "evidence": "sent to Dubai"}]},
                    {"places": [{"place": "Dubai", "order": 1, "arrival_date": None, "date_confidence": "", "time_text": "", "evidence": "sent to Dubai"}]},
                    {"places": [{"place": "Dubai", "order": 1, "arrival_date": None, "date_confidence": "", "time_text": "", "evidence": "sent to Dubai"}]},
                    {"places": [{"place": "Dubai", "order": 1, "arrival_date": None, "date_confidence": "", "time_text": "", "evidence": "sent to Dubai"}]},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client, person_name="Ahmad bin Said")
            self.assertEqual([person["name"] for person in result.people], ["Mariam bint Yusuf", "Ahmad bin Said"])
            ahmad = next(person for person in result.people if person["name"] == "Ahmad bin Said")
            self.assertEqual(ahmad["rows"][0]["Place"], "Dubai")

    def test_run_folder_only_processes_pages_with_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "ocr"
            inter_dir = root / "inter"
            in_dir.mkdir()
            inter_dir.mkdir()
            (in_dir / "p001.txt").write_text(_fixture("single_place.txt"), encoding="utf-8")
            (in_dir / "p002.txt").write_text(_fixture("ambiguous.txt"), encoding="utf-8")
            (inter_dir / "p001.classify.json").write_text(json.dumps({"page": 1, "should_extract": True, "report_type": "statement", "evidence": "Statement of slave Mariam bint Yusuf"}), encoding="utf-8")
            (inter_dir / "p002.classify.json").write_text(json.dumps({"page": 2, "should_extract": True, "report_type": "correspondence", "evidence": "Memorandum regarding Mariam bint Yusuf"}), encoding="utf-8")
            (inter_dir / "p001.names.json").write_text(json.dumps({"page": 1, "named_people": [{"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}]}), encoding="utf-8")
            (inter_dir / "p002.names.json").write_text(json.dumps({"page": 2, "named_people": []}), encoding="utf-8")
            client = FakeClient(
                [
                    {"places": [{"place": "Zanzibar", "time_text": None, "evidence": "native of Zanzibar"}]},
                    {"places": []},
                    {"places": [{"place": "Zanzibar", "order": 0, "arrival_date": None, "date_confidence": "", "time_text": "", "evidence": "native of Zanzibar"}]},
                    {"places": [{"place": "Zanzibar", "order": 0, "arrival_date": None, "date_confidence": "", "time_text": "", "evidence": "native of Zanzibar"}]},
                ]
            )
            summary = run_folder(in_dir, inter_dir, inter_dir, client=client, wait_ready=False)
            self.assertEqual(summary["total_pages"], 1)
            self.assertTrue((inter_dir / "p001.places.json").exists())
            self.assertFalse((inter_dir / "p002.places.json").exists())

    def test_run_page_file_preserves_faraj_route_order_and_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p006.txt"
            classify = root / "p006.classify.json"
            names = root / "p006.names.json"
            target = root / "p006.places.json"
            ocr.write_text(_fixture("faraj_p006.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 6, "should_extract": True, "report_type": "correspondence", "evidence": "I have the honour to report for your information and record"}), encoding="utf-8")
            names.write_text(json.dumps({"page": 6, "named_people": [{"name": "Faraj bin Said", "evidence": "a slave named Faraj bin Said"}]}), encoding="utf-8")
            client = FakeClient(
                [
                    {"places": [{"place": "Bahrein", "time_text": None, "evidence": "holder of a manumission certificate from the Pol: Agent Bahrein"}, {"place": "Koweit", "time_text": None, "evidence": "appeared before me on the 27th ultimo and complained that his master had enticed him here"}]},
                    {"places": [{"place": "Kuwait", "order": 1, "arrival_date": "1907-11-02", "date_confidence": "explicit", "time_text": None, "evidence": "The document is dated 2nd Nov 07 and mentions Faraj bin Said appeared before the author on the 27th ultimo (October 1907)."}, {"place": "Bahrain", "order": 2, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "before 1907-05-19", "evidence": "Faraj bin Said held a manumission certificate from the Political Agent in Bahrain dated 19th May 1907, implying he was in Bahrain before this date."}, {"place": "Bushire", "order": 3, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "after 1907-11-02", "evidence": "The case was to be put up to the Resident in Bushire for orders, indicating a potential movement to or involvement with Bushire."}]},
                    {"places": [{"place": "Bahrain", "order": 1, "arrival_date": None, "date_confidence": "unknown", "time_text": "before 1907-05-19", "evidence": "Held a manumission certificate from the Political Agent in Bahrain dated 19th May 1907."}, {"place": "Kuwait", "order": 2, "arrival_date": "1907-10-27", "date_confidence": "explicit", "time_text": None, "evidence": "Appeared before the author on the 27th ultimo (October 1907)."}, {"place": "Bushehr", "order": 3, "arrival_date": None, "date_confidence": "unknown", "time_text": "after 1907-11-02", "evidence": "The case was to be put up to the Resident in Bushire for orders."}]},
                    {"places": [{"place": "Bahrain", "order": 1, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "before 1907-05-19", "evidence": "Manumission certificate dated 19th May 1907."}, {"place": "Kuwait", "order": 2, "arrival_date": "1907-10-27", "date_confidence": "explicit", "time_text": None, "evidence": "Appeared before the author on the 27th October 1907."}, {"place": "Bushehr", "order": 3, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "after 1907-11-02", "evidence": "Case to be put up to the Resident in Bushire."}]},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client)
            self.assertEqual(
                [(row["Place"], row["Order"], row["Arrival Date"], row["Date Confidence"], row["Time Info"]) for row in result.rows],
                [
                    ("Bahrain", 1, "", "", "before 1907-05-19"),
                    ("Kuwait", 2, "1907-10-27", "explicit", ""),
                    ("Bushehr", 3, "", "", "after 1907-11-02"),
                ],
            )

    def test_run_page_file_drops_bogus_inferred_clause_for_abdulla(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ocr = root / "p010.txt"
            classify = root / "p010.classify.json"
            names = root / "p010.names.json"
            target = root / "p010.places.json"
            ocr.write_text(_fixture("abdulla_p010.txt"), encoding="utf-8")
            classify.write_text(json.dumps({"page": 10, "should_extract": True, "report_type": "correspondence", "evidence": "I have the honour to request that, if you see fit, the usual manumission paper may be sent to me."}), encoding="utf-8")
            names.write_text(json.dumps({"page": 10, "named_people": [{"name": "Abdulla, son of", "evidence": "Description of slave Abdulla for entry in manumission paper."}]}), encoding="utf-8")
            client = FakeClient(
                [
                    {"places": [{"place": "Bushire", "time_text": None, "evidence": "Abdulla will be conveyed to-day to H.M.S. \"Lapwing\" and the Commander requested officially by letter to give him a passage to Bushire."}, {"place": "Abyssinia", "time_text": None, "evidence": "Birth place ... Abyssinia."}, {"place": "Berbera", "time_text": None, "evidence": "Abdulla states that if he can be taken out of this place, he will find his own way to Berbera."}, {"place": "Koweit", "time_text": None, "evidence": "Copy of a letter No. 282, dated the 10th June 1907, from the Political Agent, Koweit."}]},
                    {"places": [{"place": "Abyssinia", "order": 1, "arrival_date": None, "date_confidence": "unknown", "time_text": "birthplace", "evidence": "Description of slave Abdulla for entry in manumission paper."}, {"place": "Koweit", "order": 2, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "until the Autumn, when he would sail in one of the trading buggalows", "evidence": "Copy of a letter No. 282, dated the 10th June 1907, from the Political Agent, Koweit, to the Political Resident in the Persian Gulf, Bushire."}, {"place": "Bushire", "order": 3, "arrival_date": None, "date_confidence": "derived_from_doc", "time_text": "to-day", "evidence": "Abdulla will be conveyed to-day to H.M.S. \"Lapwing\" and the Commander requested officially by letter to give him a passage to Bushire."}, {"place": "Berbera", "order": 0, "arrival_date": None, "date_confidence": "unknown", "time_text": "if he can be taken out of this place", "evidence": "Abdulla states that if he can be taken out of this place, he will find his own way to Berbera."}]},
                    {"places": [{"place": "Abyssinia", "order": 1, "arrival_date": None, "date_confidence": "unknown", "time_text": "birthplace", "evidence": "Description of slave Abdulla for entry in manumission paper."}, {"place": "Koweit", "order": 2, "arrival_date": "1907-06-10", "date_confidence": "explicit", "time_text": "until the Autumn, when he would sail in one of the trading buggalows", "evidence": "Copy of a letter No. 282, dated the 10th June 1907, from the Political Agent, Koweit."}, {"place": "Bushehr", "order": 3, "arrival_date": "1907-06-10", "date_confidence": "derived_from_doc", "time_text": "to-day", "evidence": "Abdulla will be conveyed to-day to H.M.S. \"Lapwing\" and the Commander requested officially by letter to give him a passage to Bushire."}, {"place": "Berbera", "order": 0, "arrival_date": None, "date_confidence": "unknown", "time_text": "if he can be taken out of this place", "evidence": "Abdulla states that if he can be taken out of this place, he will find his own way to Berbera."}]},
                    {"places": [{"place": "Abyssinia", "order": 1, "arrival_date": None, "date_confidence": "unknown", "time_text": "birthplace", "evidence": "Description of slave Abdulla for entry in manumission paper."}, {"place": "Koweit", "order": 2, "arrival_date": "1907-06-10", "date_confidence": "explicit", "time_text": "until the Autumn", "evidence": "Copy of a letter No. 282, dated the 10th June 1907."}, {"place": "Bushehr", "order": 3, "arrival_date": "1907-06-10", "date_confidence": "derived_from_doc", "time_text": "to-day", "evidence": "Abdulla will be conveyed to-day to H.M.S. \"Lapwing\""}, {"place": "Berbera", "order": 0, "arrival_date": None, "date_confidence": "unknown", "time_text": "if he can be taken out of this place", "evidence": "Abdulla states that if he can be taken out of this place"}]},
                ]
            )
            result = run_page_file(ocr, classify, names, target, client=client)
            self.assertEqual(
                [(row["Place"], row["Order"], row["Arrival Date"], row["Date Confidence"]) for row in result.rows],
                [
                    ("Abyssinia", 1, "", ""),
                    ("Koweit", 2, "1907-06-10", "explicit"),
                    ("Bushehr", 3, "1907-06-10", "derived_from_doc"),
                    ("Berbera", 0, "", ""),
                ],
            )
            self.assertNotIn("Without The Slightest Pressure On Either", [row["Place"] for row in result.rows])


if __name__ == "__main__":
    unittest.main()
