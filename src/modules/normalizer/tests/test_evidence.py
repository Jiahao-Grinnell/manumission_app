from __future__ import annotations

import unittest

from modules.normalizer.evidence import clean_evidence, normalize_for_match


class EvidenceTests(unittest.TestCase):
    def test_clean_evidence_truncates_to_25_words(self) -> None:
        text = " ".join(f"w{i}" for i in range(30))
        self.assertEqual(len(clean_evidence(text).split()), 25)

    def test_normalize_for_match_strips_accents_and_punctuation(self) -> None:
        self.assertEqual(normalize_for_match("Āmina, bint Yusuf!"), "amina bint yusuf")


if __name__ == "__main__":
    unittest.main()
