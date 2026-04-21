from __future__ import annotations

import unittest

from modules.metadata_extractor.vocab import _ordered_list


class VocabTests(unittest.TestCase):
    def test_boolean_yaml_values_map_to_yes_no(self) -> None:
        values = _ordered_list({"whether_abuse_values": [True, False]}, "whether_abuse_values", ["yes", "no"])
        self.assertEqual(values, ["yes", "no"])


if __name__ == "__main__":
    unittest.main()
