from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.place_extractor.standalone import create_app


def _payload() -> dict:
    return {
        "doc_id": "sample input 1",
        "page": 6,
        "selected_name": "Mariam bint Yusuf",
        "selected_person": {
            "name": "Mariam bint Yusuf",
            "rows": [
                {
                    "Name": "Mariam bint Yusuf",
                    "Page": 6,
                    "Place": "Bushehr",
                    "Order": 1,
                    "Arrival Date": "",
                    "Date Confidence": "",
                    "Time Info": "",
                    "_evidence": "forwarded by the Political Agency, Bushire",
                }
            ],
        },
        "result": {
            "rows": [
                {
                    "Name": "Mariam bint Yusuf",
                    "Page": 6,
                    "Place": "Bushehr",
                    "Order": 1,
                    "Arrival Date": "",
                    "Date Confidence": "",
                    "Time Info": "",
                    "_evidence": "forwarded by the Political Agency, Bushire",
                },
                {
                    "Name": "Mariam bint Yusuf",
                    "Page": 6,
                    "Place": "Dubai",
                    "Order": 2,
                    "Arrival Date": "1931-05-17",
                    "Date Confidence": "explicit",
                    "Time Info": "17th May 1931",
                    "_evidence": "arriving Dubai about the 17th May 1931",
                },
            ]
        },
    }


class BlueprintDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = self.app.test_client()

    def test_download_page_csv_returns_all_rows_without_evidence_column(self) -> None:
        with patch("modules.place_extractor.blueprint._page_payload", return_value=_payload()):
            response = self.client.get("/places/download/sample%20input%201/6.csv")

        self.assertEqual(response.status_code, 200)
        text = response.data.decode("utf-8-sig")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Name,Page,Place,Order,Arrival Date,Date Confidence,Time Info")
        self.assertIn("Mariam bint Yusuf,6,Bushehr,1,,,", text)
        self.assertIn("Mariam bint Yusuf,6,Dubai,2,1931-05-17,explicit,17th May 1931", text)
        self.assertNotIn("_evidence", text)
        self.assertIn("sample input 1_p006_places.csv", response.headers["Content-Disposition"])

    def test_download_person_csv_returns_selected_person_rows(self) -> None:
        with patch("modules.place_extractor.blueprint._page_payload", return_value=_payload()):
            response = self.client.get("/places/download/sample%20input%201/6.csv?name=Mariam%20bint%20Yusuf")

        self.assertEqual(response.status_code, 200)
        text = response.data.decode("utf-8-sig")
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Mariam bint Yusuf,6,Bushehr,1,,,", text)
        self.assertNotIn("Dubai", text)
        self.assertIn("sample input 1_p006_mariam_bint_yusuf_places.csv", response.headers["Content-Disposition"])

    def test_download_person_csv_rejects_unknown_name(self) -> None:
        payload = _payload()
        payload["selected_name"] = "Fatima bint Ali"

        with patch("modules.place_extractor.blueprint._page_payload", return_value=payload):
            response = self.client.get("/places/download/sample%20input%201/6.csv?name=Mariam%20bint%20Yusuf")

        self.assertEqual(response.status_code, 404)

    def test_clear_all_results_deletes_only_places_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inter_dir = Path(tmpdir)
            (inter_dir / "p001.places.json").write_text("{}", encoding="utf-8")
            (inter_dir / "p002.places.json").write_text("{}", encoding="utf-8")
            (inter_dir / "p001.names.json").write_text("{}", encoding="utf-8")
            (inter_dir / "p001.classify.json").write_text("{}", encoding="utf-8")
            (inter_dir / "notes.txt").write_text("keep me", encoding="utf-8")
            fake_paths = SimpleNamespace(doc_id="sample input 1", inter_dir=inter_dir)

            with patch("modules.place_extractor.blueprint.doc_paths", return_value=fake_paths):
                response = self.client.post("/places/clear-all/sample%20input%201")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"doc_id": "sample input 1", "deleted_files": 2})
            self.assertFalse((inter_dir / "p001.places.json").exists())
            self.assertFalse((inter_dir / "p002.places.json").exists())
            self.assertTrue((inter_dir / "p001.names.json").exists())
            self.assertTrue((inter_dir / "p001.classify.json").exists())
            self.assertTrue((inter_dir / "notes.txt").exists())


if __name__ == "__main__":
    unittest.main()
