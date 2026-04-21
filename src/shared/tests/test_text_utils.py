from __future__ import annotations

import unittest

from shared.text_utils import clean_ocr, extract_json, normalize_ws, render_prompt, strip_accents


class TextUtilsTests(unittest.TestCase):
    def test_normalize_ws(self) -> None:
        self.assertEqual(normalize_ws("  a\t b\n c  "), "a b c")

    def test_strip_accents(self) -> None:
        self.assertEqual(strip_accents("Zanzíbar"), "Zanzibar")

    def test_clean_ocr(self) -> None:
        self.assertEqual(clean_ocr("\ufeff a\t\tb \r\n\n c  "), "a b\n\nc")

    def test_extract_plain_json_object(self) -> None:
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_extract_fenced_json_array(self) -> None:
        self.assertEqual(extract_json('noise\n```json\n[{"a": 1}]\n```\nmore'), [{"a": 1}])

    def test_extract_json_with_braces_inside_string(self) -> None:
        text = 'prefix {"text": "value with } brace", "items": [1, 2]} suffix {"bad":'
        self.assertEqual(extract_json(text), {"text": "value with } brace", "items": [1, 2]})

    def test_extract_returns_none_for_missing_json(self) -> None:
        self.assertIsNone(extract_json("no structured payload here"))

    def test_render_prompt_replaces_named_placeholder(self) -> None:
        self.assertEqual(render_prompt("hello {name}", name="world"), "hello world")

    def test_render_prompt_leaves_literal_json_braces_intact(self) -> None:
        template = 'Return JSON only:\n{"ok": true, "text": "{ocr}"}'
        self.assertEqual(
            render_prompt(template, ocr="sample"),
            'Return JSON only:\n{"ok": true, "text": "sample"}',
        )


if __name__ == "__main__":
    unittest.main()
