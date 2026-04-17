from __future__ import annotations

import unittest

from shared.paths import doc_paths, normalize_doc_id


class PathTests(unittest.TestCase):
    def test_doc_paths_use_expected_layout(self) -> None:
        paths = doc_paths("docABC")
        self.assertEqual(str(paths.pdf), "/data/input_pdfs/docABC.pdf")
        self.assertEqual(str(paths.page_image(3)), "/data/pages/docABC/p003.png")
        self.assertEqual(str(paths.ocr_text(3)), "/data/ocr_text/docABC/p003.txt")
        self.assertEqual(str(paths.classify(3)), "/data/intermediate/docABC/p003.classify.json")

    def test_doc_id_sanitizes_path_separators(self) -> None:
        self.assertEqual(normalize_doc_id("volume/part:one"), "volume_part_one")

    def test_doc_id_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            normalize_doc_id("   ")


if __name__ == "__main__":
    unittest.main()

