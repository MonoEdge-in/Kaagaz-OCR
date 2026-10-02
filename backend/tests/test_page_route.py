"""R1: the route stored for each page, with its reason code (ML 12).

kaagaz/reading/route.py: every branch of ``route_for_page``.
"""

import unittest
from typing import get_args

from kaagaz.reading.route import Reason, Route, RouteDecision, route_for_page

FAILED = RouteDecision("ocr", "scan_image")  # what text_layer_ok said for a page it sent to OCR
PASSED = RouteDecision("text_layer", "ok")


class RouteForPageTest(unittest.TestCase):
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

    def test_a_sheet_page_is_cells_whatever_else_is_known(self) -> None:
        for blank in (True, False):
            for ocr_on in (True, False):
                with self.subTest(blank=blank, ocr_on=ocr_on):
                    decision = route_for_page(unit="cells", origins={"cell"}, blank=blank, ocr_on=ocr_on)
                    self.assertEqual(decision, RouteDecision("cells", "ok"))

    def test_a_page_with_ocr_spans_is_ocr_with_the_reason_it_was_sent(self) -> None:
        decision = route_for_page(unit="pt", origins={"ocr"}, blank=False, ocr_on=True, check=FAILED)
        self.assertEqual(decision, RouteDecision("ocr", "scan_image"))

    def test_a_photo_read_by_ocr_has_no_text_layer_so_few_chars(self) -> None:
        decision = route_for_page(unit="px", origins={"ocr"}, blank=False, ocr_on=True)
        self.assertEqual(decision, RouteDecision("ocr", "few_chars"))

    def test_ocr_spans_win_over_text_layer_spans(self) -> None:
        decision = route_for_page(unit="pt", origins=["text_layer", "ocr"], blank=False, ocr_on=True)
        self.assertEqual(decision, RouteDecision("ocr", "few_chars"))

    def test_a_page_with_text_layer_spans_is_text_layer(self) -> None:
        for check in (None, PASSED):
            with self.subTest(check=check):
                decision = route_for_page(
                    unit="pt", origins={"text_layer"}, blank=False, ocr_on=True, check=check
                )
                self.assertEqual(decision, RouteDecision("text_layer", "ok"))

    def test_a_text_layer_kept_after_failing_the_check_says_why_it_failed(self) -> None:
        # No engine, or the engine found nothing, or the drawn page was blank.
        for ocr_on in (True, False):
            with self.subTest(ocr_on=ocr_on):
                decision = route_for_page(
                    unit="pt", origins={"text_layer"}, blank=True, ocr_on=ocr_on, check=FAILED
                )
                self.assertEqual(decision, RouteDecision("text_layer", "scan_image"))

    def test_a_page_with_no_spans_and_a_blank_picture_is_blank(self) -> None:
        for ocr_on in (True, False):
            for check in (None, FAILED):
                with self.subTest(ocr_on=ocr_on, check=check):
                    decision = route_for_page(
                        unit="pt", origins=set(), blank=True, ocr_on=ocr_on, check=check
                    )
                    self.assertEqual(decision, RouteDecision("blank", "blank"))

    def test_a_page_sent_to_ocr_that_found_nothing_is_still_ocr(self) -> None:
        decision = route_for_page(unit="pt", origins=(), blank=False, ocr_on=True, check=FAILED)
        self.assertEqual(decision, RouteDecision("ocr", "scan_image"))
        decision = route_for_page(unit="px", origins=(), blank=False, ocr_on=True)
        self.assertEqual(decision, RouteDecision("ocr", "few_chars"))

    def test_a_page_with_no_spans_and_no_engine_was_not_read(self) -> None:
        self.assertIsNone(route_for_page(unit="pt", origins=set(), blank=False, ocr_on=False, check=FAILED))

    def test_spans_with_no_known_origin_give_no_route(self) -> None:
        # Stored before spans carried an origin: never guessed as blank or OCR.
        self.assertIsNone(route_for_page(unit="pt", origins=[None], blank=True, ocr_on=True))


if __name__ == "__main__":
    unittest.main()
