"""OCR module."""

from .core import OcrResult, ocr_page, run_folder, should_skip_existing

__all__ = ["OcrResult", "ocr_page", "run_folder", "should_skip_existing"]
