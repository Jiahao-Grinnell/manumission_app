from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from modules.ocr.core import cleanup_ocr_text, ocr_page, run_folder, should_skip_existing
from modules.ocr.preprocessing import crop_foreground, deskew, enhance_gray, preprocess_page, split_vertical_with_overlap


def _sample_image(width: int = 900, height: int = 1200) -> np.ndarray:
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(img, "Sample OCR Text", (80, 220), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4)
    cv2.putText(img, "Second line", (80, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    return img


class OcrPreprocessingTests(unittest.TestCase):
    def test_preprocessing_functions_return_valid_images(self) -> None:
        img = _sample_image()
        enhanced = enhance_gray(img, target_long=1000)
        self.assertEqual(len(enhanced.shape), 2)
        deskewed = deskew(enhanced)
        cropped, box = crop_foreground(deskewed)
        self.assertGreater(cropped.shape[0], 0)
        self.assertGreater(box[2], 0)
        result = preprocess_page(img, preprocess_long=1000, min_long_for_ocr=800, tile=True)
        self.assertGreaterEqual(len(result.tiles_bgr), 1)

    def test_split_vertical_with_overlap(self) -> None:
        img = _sample_image()
        tiles = split_vertical_with_overlap(img, parts=2, overlap_px=100)
        self.assertEqual(len(tiles), 2)
        self.assertGreater(tiles[0].shape[0], img.shape[0] // 2)

    def test_cleanup_and_skip_existing(self) -> None:
        self.assertEqual(cleanup_ocr_text("```text\nHello\n```"), "Hello")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p001.txt"
            self.assertFalse(should_skip_existing(path))
            path.write_text("[OCR_EMPTY]", encoding="utf-8")
            self.assertTrue(should_skip_existing(path))
            path.write_text("", encoding="utf-8")
            self.assertFalse(should_skip_existing(path))

    def test_ocr_page_with_mocked_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "p001.png"
            out = root / "p001.txt"
            cv2.imwrite(str(image), _sample_image())
            with mock.patch("modules.ocr.core.ollama_ocr_one_image", return_value="Sample OCR Text"):
                result = ocr_page(image, out, model="mock", ollama_generate_url="http://ollama:11434/api/generate", min_long_for_ocr=800)
            self.assertEqual(result.status, "done")
            self.assertIn("Sample OCR Text", out.read_text(encoding="utf-8"))

    def test_run_folder_resume_with_existing_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "pages"
            out_dir = root / "ocr"
            in_dir.mkdir()
            cv2.imwrite(str(in_dir / "p001.png"), _sample_image())
            out_dir.mkdir()
            (out_dir / "p001.txt").write_text("[OCR_EMPTY]", encoding="utf-8")
            manifest = run_folder(in_dir, out_dir, model="mock", wait_ready=False, resume=True)
            self.assertEqual(manifest["completed_pages"], 1)
            self.assertEqual(manifest["pages"][0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
