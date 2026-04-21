from __future__ import annotations

import unittest

from modules.metadata_extractor.parsing import choose_allowed, choose_yes_no_blank, parse_meta
from modules.metadata_extractor.vocab import CONFLICT_TYPES, CRIME_TYPES, DETAIL_REPORT_TYPES, TRIAL_TYPES


class ParsingTests(unittest.TestCase):
    def test_choose_allowed_uses_yaml_allowlist_case_insensitively(self) -> None:
        self.assertEqual(choose_allowed("Statement", DETAIL_REPORT_TYPES), "statement")
        self.assertEqual(choose_allowed("KIDNAPPING", CRIME_TYPES), "kidnapping")
        self.assertEqual(choose_allowed("nope", CRIME_TYPES), "")

    def test_choose_yes_no_blank_normalizes_values(self) -> None:
        self.assertEqual(choose_yes_no_blank(" YES "), "yes")
        self.assertEqual(choose_yes_no_blank("No"), "no")
        self.assertEqual(choose_yes_no_blank("maybe"), "")

    def test_parse_meta_clears_invalid_values_and_missing_evidence(self) -> None:
        parsed = parse_meta(
            {
                "report_type": "memo",
                "crime_type": "kidnapping",
                "whether_abuse": "yes",
                "conflict_type": "ownership dispute",
                "trial": "freedom/manumission outcome",
                "amount_paid": "Rs. 20",
                "evidence": {
                    "crime_type": "kidnapped from Zanzibar",
                    "whether_abuse": None,
                    "conflict_type": "ownership in dispute",
                    "trial": None,
                    "amount_paid": "Rs. 20 was paid",
                },
            },
            "Mariam bint Yusuf",
            12,
            "statement",
            classify_evidence="Statement of slave Mariam bint Yusuf",
        )
        row = parsed["row"]
        self.assertEqual(row["Report Type"], "statement")
        self.assertEqual(row["Crime Type"], "kidnapping")
        self.assertEqual(row["Whether abuse"], "")
        self.assertEqual(row["Conflict Type"], "ownership dispute")
        self.assertEqual(row["Trial"], "")
        self.assertEqual(row["Amount paid"], "Rs. 20")
        self.assertEqual(parsed["validation"]["report_type"]["status"], "inherited")
        self.assertEqual(parsed["validation"]["whether_abuse"]["status"], "cleared_missing_evidence")
        self.assertEqual(parsed["validation"]["trial"]["status"], "cleared_missing_evidence")

    def test_parse_meta_handles_non_dict_payload(self) -> None:
        parsed = parse_meta(["bad"], "Ahmad bin Said", 5, "correspondence")
        self.assertEqual(parsed["row"]["Report Type"], "correspondence")
        self.assertEqual(parsed["row"]["Crime Type"], "")
        self.assertEqual(parsed["validation"]["crime_type"]["status"], "empty")


if __name__ == "__main__":
    unittest.main()
