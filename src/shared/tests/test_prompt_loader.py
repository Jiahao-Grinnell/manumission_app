from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shared.prompt_loader import load_prompt_text


class PromptLoaderTests(unittest.TestCase):
    def test_loads_nested_prompt_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "page_classifier"
            nested.mkdir(parents=True)
            (nested / "page_classify.txt").write_text("nested prompt", encoding="utf-8")
            (root / "page_classify.txt").write_text("legacy prompt", encoding="utf-8")

            self.assertEqual(
                load_prompt_text(
                    "page_classifier",
                    "page_classify.txt",
                    prompt_dir=root,
                    legacy_names=["page_classify.txt"],
                ),
                "nested prompt",
            )

    def test_falls_back_to_legacy_prompt_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ocr.txt").write_text("legacy prompt", encoding="utf-8")

            self.assertEqual(
                load_prompt_text(
                    "ocr",
                    "ocr.txt",
                    prompt_dir=root,
                    legacy_names=["ocr.txt"],
                ),
                "legacy prompt",
            )

    def test_returns_fallback_when_no_prompt_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                load_prompt_text("shared", "json_repair.txt", prompt_dir=root, fallback_text="fallback"),
                "fallback",
            )


if __name__ == "__main__":
    unittest.main()
