"""The identifiers a document is linked by (PRD 2 D5, security amendment A7)."""

import unittest

from kaagaz.extraction.links import (
    MAX_IDENTIFIER_CHARS,
    MAX_LINK_IDENTIFIERS,
    link_identifiers,
    normalise_identifier,
)
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import LinkIdentifier
from kaagaz.reading.spans import BoxLocation

BOX = BoxLocation(1, 0.1, 0.1, 0.9, 0.12)


def value(field: str, text: str, record_key: str = "card") -> ExtractedValue:
    return ExtractedValue(record_key, field, text, text, BOX)


class NormaliseIdentifierTest(unittest.TestCase):
    def test_every_space_is_removed_and_case_ignored(self) -> None:
        self.assertEqual(normalise_identifier(" INV 17 "), "inv17")
        self.assertEqual(normalise_identifier("INV\t-\n17"), "inv-17")
        self.assertEqual(normalise_identifier("INV\N{NO-BREAK SPACE}17"), "inv17")
        self.assertEqual(normalise_identifier("PO/2026/0009"), "po/2026/0009")

    def test_devanagari_digits_become_ascii_digits(self) -> None:
        """Council fix 10: ४५/२ and 45/2 are one survey number."""
        self.assertEqual(normalise_identifier("गट नं. ४५/२"), normalise_identifier("गट नं. 45/2"))
        self.assertEqual(normalise_identifier("४५/२"), "45/2")


class LinkIdentifiersTest(unittest.TestCase):
    def test_only_the_seven_kinds_from_the_card_and_document_records(self) -> None:
        values = [
            value("invoice_number", "INV 17"),
            value("po_number", "PO-9", record_key="document"),
            value("challan_number", "CH 4"),
            value("survey_number", "४५/२"),
            value("khata_number", "K 88"),
            value("account_number", "0012 3456"),
            value("reference_number", "Ref 5"),
            value("gstin", "27AAPFU0939F1ZV"),  # never a link: one supplier's every invoice would link
            value("pan", "AAPFU0939F"),
            value("invoice_number", "INV 18", record_key="page:2:line:3"),  # not the document's own
            value("total_amount", "1,180.00"),
            value("other", "X-1"),
        ]
        self.assertEqual(
            link_identifiers(values),
            (
                LinkIdentifier("account_number", "00123456"),
                LinkIdentifier("challan_number", "ch4"),
                LinkIdentifier("invoice_number", "inv17"),
                LinkIdentifier("khata_number", "k88"),
                LinkIdentifier("po_number", "po-9"),
                LinkIdentifier("reference_number", "ref5"),
                LinkIdentifier("survey_number", "45/2"),
            ),
        )

    def test_repeats_are_kept_once(self) -> None:
        values = [
            value("invoice_number", "INV 17"),
            value("invoice_number", "inv17", record_key="document"),
            value("po_number", "INV 17"),
        ]
        self.assertEqual(
            link_identifiers(values),
            (LinkIdentifier("invoice_number", "inv17"), LinkIdentifier("po_number", "inv17")),
        )

    def test_an_empty_or_over_long_identifier_is_left_out(self) -> None:
        longest = "A" * MAX_IDENTIFIER_CHARS
        values = [
            value("invoice_number", " \t "),
            value("invoice_number", longest),
            value("po_number", "B" * (MAX_IDENTIFIER_CHARS + 1)),
        ]
        self.assertEqual(MAX_IDENTIFIER_CHARS, 64)
        self.assertEqual(link_identifiers(values), (LinkIdentifier("invoice_number", "a" * 64),))

    def test_at_most_30_identifiers_the_first_in_order(self) -> None:
        values = [value("reference_number", f"R{n:02d}") for n in range(35, 0, -1)]
        links = link_identifiers(values)
        self.assertEqual(MAX_LINK_IDENTIFIERS, 30)
        self.assertEqual(len(links), 30)
        self.assertEqual((links[0].value, links[-1].value), ("r01", "r30"))

    def test_a_form_name_is_never_a_link(self) -> None:
        """Council fix 10 (PROVISIONAL list): "7/12" names the land record form itself, so as a
        survey number it would link every land record to every other."""
        values = [
            value("survey_number", "७/१२"),
            value("survey_number", "7 / 12"),
            value("khata_number", "8-A"),
        ]
        self.assertEqual(link_identifiers(values), ())

    def test_no_values_no_links(self) -> None:
        self.assertEqual(link_identifiers([]), ())
