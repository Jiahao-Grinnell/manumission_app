from __future__ import annotations

import unittest

from modules.normalizer.names import merge_named_people, name_compare_tokens, names_maybe_same_person, normalize_name


class NameTests(unittest.TestCase):
    def test_normalize_name_keeps_connectors_lowercase(self) -> None:
        self.assertEqual(normalize_name("  mariam   BINT   YUSUF  "), "Mariam bint Yusuf")

    def test_normalize_name_strips_prefixes_and_accents(self) -> None:
        self.assertEqual(normalize_name("the slave āmina ibn salim"), "Amina bin Salim")

    def test_name_compare_tokens_skip_connectors(self) -> None:
        self.assertEqual(name_compare_tokens("Mariam bint Yusuf"), ["mariam", "yusuf"])

    def test_names_maybe_same_person_accepts_close_spellings(self) -> None:
        self.assertTrue(names_maybe_same_person("Mariam bint Yusuf", "Marium bint Yousuf"))

    def test_merge_named_people_clusters_variants(self) -> None:
        merged = merge_named_people(
            [{"name": "Mariam bint Yusuf", "evidence": "short"}],
            [{"name": "Marium bint Yousuf", "evidence": "longer evidence"}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "Marium bint Yousuf")


if __name__ == "__main__":
    unittest.main()
