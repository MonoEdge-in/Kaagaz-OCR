"""R1: the route stored for each page, with its reason code (ML 12).

kaagaz/reading/route.py: the route and reason names are the stored and printed ones.
"""

import unittest
from typing import get_args

from kaagaz.reading.route import Reason, Route


class RouteNamesTest(unittest.TestCase):
    def test_the_routes_and_reasons_are_the_stored_and_printed_ones(self) -> None:
        self.assertEqual(get_args(Route), ("cells", "text_layer", "ocr", "blank"))  # pages.route CHECK (0004)
        self.assertEqual(
            get_args(Reason),
            (
                "few_chars",
                "bad_chars",
                "scan_image",
                "hidden_text",
                "legacy_font",
                "map_errors",
                "malformed_devanagari",
                "blank",
                "ok",
            ),
        )


if __name__ == "__main__":
    unittest.main()
