from __future__ import annotations

import unittest

from shared.config import settings


class ConfigTests(unittest.TestCase):
    def test_default_models_are_configured(self) -> None:
        self.assertEqual(settings.OLLAMA_MODEL, "qwen2.5:14b-instruct")
        self.assertEqual(settings.OCR_MODEL, "glm-ocr:latest")


if __name__ == "__main__":
    unittest.main()

