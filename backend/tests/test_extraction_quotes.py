"""The quote finder (PRD 2 D2, ML amendment 9, test-engineer amendment 15).

A model quote is found in the first reader's text after both sides are
normalised, and the stored text is always the reader's text at the match.
Every expected value is a literal written by hand, except in the seeded
round-trip property, whose expected text is built by the test from the
pieces it generated, never by the code under test.
"""

import random
import unittest
from bisect import bisect_left
from dataclasses import dataclass

from kaagaz.extraction.quotes import (
    LABEL_WINDOW,
    MAX_QUOTE_CHARS,
    QuoteCounts,
    ReaderText,
    find_labelled,
    find_quote,
    located_value,
    normalise_quote,
    reader_text,
)
from kaagaz.extraction.values import ExtractedValue, QuoteMatch
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, Span

NBSP = "\N{NO-BREAK SPACE}"
ZWJ, ZWNJ = "\N{ZERO WIDTH JOINER}", "\N{ZERO WIDTH NON-JOINER}"
NUKTA = "\N{DEVANAGARI SIGN NUKTA}"
FI = "\N{LATIN SMALL LIGATURE FI}"
HALF = "\N{VULGAR FRACTION ONE HALF}"  # NFKC: 1, FRACTION SLASH, 2
FRACTION_SLASH = "\N{FRACTION SLASH}"
SHARP_S = "\N{LATIN SMALL LETTER SHARP S}"  # casefold: ss
DOTTED_I = "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}"  # casefold: i + COMBINING DOT ABOVE


def line(text: str, page: int = 1, y: float = 0.1, *, x0: float = 0.1, x1: float = 0.9) -> Span:
    return Span(text, text, BoxLocation(page, x0, y, x1, y + 0.02))


def cell(text: str, row: int, column: int) -> Span:
    return Span(text, text, CellLocation(1, row, column))


@dataclass(frozen=True, slots=True)
class OcrSpan(Span):
    """A span with the fields PR 2 adds to Span (origin, table_id, row), for these tests only."""

    origin: str | None = None
    table_id: str | None = None
    row: int | None = None


def only(reader: ReaderText, quote: str) -> str:
    found = find_quote(reader, quote)
    assert found is not None, quote
    return found.text


class NormaliseQuoteTest(unittest.TestCase):
    def test_the_d2_rules(self) -> None:
        cases = {
            "  Grand" + NBSP + "Total\t Rs." + NBSP + NBSP + "1,180 ": "grand total rs. 1,180",
            "INVOICE No": "invoice no",
            "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH COMMA}\N{FULLWIDTH DIGIT TWO}": "1,2",
            "₹ १,२३,४५६.७८": "₹ 1,23,456.78",
            FI + "le": "file",
            HALF: "1" + FRACTION_SLASH + "2",
            "Stra" + SHARP_S + "e": "strasse",
            "क्" + ZWJ + "ष": "क्ष",
            "a\N{SOFT HYPHEN}b": "ab",
        }
        for quote, expected in cases.items():
            with self.subTest(quote=quote):
                self.assertEqual(normalise_quote(quote), expected)

    def test_dashes_and_curly_quotes_are_unified(self) -> None:
        # ML 9(b): U+2010 to U+2015 and the minus sign become "-"; curly quotes become straight.
        dashes = "".join(chr(c) for c in range(0x2010, 0x2016)) + "\N{MINUS SIGN}"
        self.assertEqual(normalise_quote(dashes), "-------")
        self.assertEqual(
            normalise_quote("\N{LEFT DOUBLE QUOTATION MARK}Patil\N{RIGHT DOUBLE QUOTATION MARK}"), '"patil"'
        )
        self.assertEqual(
            normalise_quote("\N{LEFT SINGLE QUOTATION MARK}A\N{RIGHT SINGLE QUOTATION MARK}"), "'a'"
        )

    def test_casefold_is_for_latin_only(self) -> None:
        self.assertEqual(normalise_quote("ÉTÉ " + DOTTED_I), "été i\N{COMBINING DOT ABOVE}")
        self.assertEqual(normalise_quote("ΣΙΓΜΑ"), "ΣΙΓΜΑ")

    def test_the_prompt_escapes_are_undone(self) -> None:
        self.assertEqual(normalise_quote("Patil &amp; Sons &lt;Pune&gt;"), "patil & sons <pune>")
        self.assertEqual(normalise_quote("&amp;lt;"), "&lt;")


class FindQuoteTest(unittest.TestCase):
    def test_a_made_up_quote_is_not_found(self) -> None:
        reader = reader_text([line("Invoice No: INV-17"), line("Total 1,180.00", y=0.5)])
        self.assertIsNone(find_quote(reader, "INV-18"))
        self.assertIsNone(find_quote(reader, "Total 1,190.00"))

    def test_an_empty_quote_is_not_found(self) -> None:
        reader = reader_text([line("Total 1,180.00")])
        for quote in ("", "   ", ZWJ + ZWNJ):
            with self.subTest(quote=quote):
                self.assertIsNone(find_quote(reader, quote))

    def test_different_spacing_is_found_and_the_reader_text_is_kept(self) -> None:
        printed = "Grand  Total:\tRs." + NBSP + "1,180.00"
        reader = reader_text([line(printed)])
        self.assertEqual(only(reader, "grand total: rs. 1,180.00"), printed)

    def test_devanagari_digits_match_ascii_digits_and_the_printed_digits_are_kept(self) -> None:
        reader = reader_text([line("एकूण ₹ १,२३,४५६.७८"), line("Total 7/12", y=0.5)])
        self.assertEqual(only(reader, "₹ 1,23,456.78"), "₹ १,२३,४५६.७८")
        self.assertEqual(only(reader, "७/१२"), "7/12")

    def test_full_width_digits_are_found_through_nfkc(self) -> None:
        printed = (
            "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH COMMA}\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT EIGHT}0"
        )
        reader = reader_text([line("Total " + printed)])
        self.assertEqual(only(reader, "1,180"), printed)

    def test_latin_is_case_insensitive_and_other_scripts_are_not(self) -> None:
        reader = reader_text([line("INVOICE NO INV-17"), line("ΣΙΓΜΑ", y=0.5)])
        self.assertEqual(only(reader, "invoice no inv-17"), "INVOICE NO INV-17")
        self.assertIsNone(find_quote(reader, "σιγμα"))

    def test_escaped_markup_in_the_quote_is_found(self) -> None:
        reader = reader_text([line("Patil & Sons <Pune>")])
        self.assertEqual(only(reader, "Patil &amp; Sons &lt;Pune&gt;"), "Patil & Sons <Pune>")

    def test_dashes_and_curly_quotes_match_their_plain_forms(self) -> None:
        printed = "INV\N{EN DASH}17 \N{LEFT DOUBLE QUOTATION MARK}Patil\N{RIGHT DOUBLE QUOTATION MARK}"
        reader = reader_text([line(printed), line("Balance \N{MINUS SIGN}500", y=0.5)])
        self.assertEqual(only(reader, 'INV-17 "Patil"'), printed)
        self.assertEqual(only(reader, "-500"), "\N{MINUS SIGN}500")

    def test_12000_is_not_found_inside_a_longer_number(self) -> None:
        reader = reader_text([line("Total 12,000.50"), line("Paid 1,12,000", y=0.5)])
        self.assertIsNone(find_quote(reader, "12,000"))
        self.assertEqual(only(reader_text([line("It was 12,000. Then more.")]), "12,000"), "12,000")

    def test_a_part_of_a_word_is_not_found(self) -> None:
        reader = reader_text([line("पाटील ट्रेडर्स"), line("Totals 17", y=0.5)])
        self.assertIsNone(find_quote(reader, "पाट"))
        self.assertIsNone(find_quote(reader, "पाटी"))
        self.assertIsNone(find_quote(reader, "Total"))
        self.assertEqual(only(reader, "पाटील"), "पाटील")

    def test_a_quote_across_lines_joins_the_reader_slices_with_one_space(self) -> None:
        reader = reader_text([line("Profit of the  month"), line("Sept is $12,000", y=0.2)])
        self.assertEqual(only(reader, "month Sept is"), "month Sept is")
        self.assertEqual(only(reader, "the month sept"), "the  month Sept")

    def test_a_literal_bar_inside_a_cell_is_kept_and_our_join_becomes_one_space(self) -> None:
        reader = reader_text([cell("Item | Code", 1, 1), cell("Qty", 1, 2), cell("Cotton", 2, 1)])
        self.assertEqual(reader.text, "Item | Code | Qty\nCotton")
        found = find_quote(reader, "Item | Code | Qty")
        assert found is not None
        self.assertEqual(found.text, "Item | Code Qty")
        self.assertEqual(found.location, CellLocation(1, 1, 1))

    def test_a_quote_starting_or_ending_in_our_join_is_refused(self) -> None:
        reader = reader_text([cell("Item", 1, 1), cell("Qty", 1, 2)])
        self.assertIsNone(find_quote(reader, "| Qty"))
        self.assertIsNone(find_quote(reader, "Item |"))

    def test_a_cell_match_gives_the_cell(self) -> None:
        reader = reader_text([cell("Total", 4, 1), cell("1,180.00", 4, 2)])
        found = find_quote(reader, "1,180.00")
        assert found is not None
        self.assertEqual(found.location, CellLocation(1, 4, 2))

    def test_found_says_how_many_spans_and_pages_it_covers_and_whether_whole(self) -> None:
        """Council fix 5: the labeller must know when a quote is not one whole span."""
        reader = reader_text([cell("Patil", 2, 1), cell("500 kg", 2, 2), line("Total", 3), line("1,000", 4)])
        cases = {
            "Patil": (1, 1, True),
            "500": (1, 1, False),
            "Patil | 500 kg": (2, 1, False),
            "Total 1,000": (2, 2, False),
        }
        for quote, expected in cases.items():
            with self.subTest(quote=quote):
                found = find_quote(reader, quote)
                assert found is not None
                self.assertEqual((found.spans, found.pages, found.whole), expected)

    def test_a_located_value_carries_the_readers_text_spot_and_match(self) -> None:
        reader = reader_text([line("Grand Total: 1,180.00"), line("Paid 1,180.00", y=0.2)])
        label, found = find_labelled(reader, "Grand Total:", "1,180.00")
        assert label is not None and found is not None
        value = located_value(found, record_key="document", field="total_amount", label=label)
        self.assertEqual(
            value,
            ExtractedValue(
                "document",
                "total_amount",
                "1,180.00",
                "1,180.00",
                found.location,
                label_location=label.location,
                quote=QuoteMatch(spans=1, pages=1, whole=False),
            ),
        )

    def test_a_located_value_carries_loose_and_ambiguous(self) -> None:
        """Council fix 6: the labeller needs both."""
        loose = find_quote(reader_text([line("कूल 500")]), "कुल")
        twice = find_quote(reader_text([line("Total 500"), line("Paid 500", y=0.2)]), "500")
        assert loose is not None and twice is not None
        self.assertEqual(
            located_value(loose, record_key="document", field="description").quote,
            QuoteMatch(spans=1, pages=1, whole=False, loose=True, ambiguous=False),
        )
        self.assertEqual(
            located_value(twice, record_key="line:1", field="amount").quote,
            QuoteMatch(spans=1, pages=1, whole=False, loose=False, ambiguous=True),
        )

    def test_the_location_is_the_union_box_on_the_first_matched_page(self) -> None:
        reader = reader_text(
            [
                Span("Seller: Patil", "Seller: Patil", BoxLocation(1, 0.1, 0.80, 0.5, 0.82)),
                Span("Traders Pune", "Traders Pune", BoxLocation(1, 0.2, 0.85, 0.7, 0.87)),
                Span("GSTIN 27", "GSTIN 27", BoxLocation(2, 0.1, 0.05, 0.9, 0.07)),
            ]
        )
        found = find_quote(reader, "Patil Traders Pune GSTIN")
        assert found is not None
        self.assertEqual(found.location, BoxLocation(1, 0.1, 0.80, 0.7, 0.87))
        self.assertEqual(found.text, "Patil Traders Pune GSTIN")

    def test_confidence_is_the_lowest_of_the_matched_spans(self) -> None:
        box = BoxLocation(1, 0.1, 0.1, 0.9, 0.12)
        low = BoxLocation(1, 0.1, 0.2, 0.9, 0.22)
        reader = reader_text([Span("Net", "Net", box, 0.95), Span("Amount", "Amount", low, 0.80)])
        found = find_quote(reader, "Net Amount")
        assert found is not None
        self.assertEqual(found.confidence, 0.80)
        direct = find_quote(reader_text([line("Net Amount")]), "Net Amount")
        assert direct is not None
        self.assertIsNone(direct.confidence)

    def test_the_origin_comes_from_the_spans_and_ocr_wins(self) -> None:
        box = BoxLocation(1, 0.1, 0.1, 0.9, 0.12)
        text_layer = OcrSpan("Net", "Net", box, origin="text_layer")
        ocr = OcrSpan("Amount", "Amount", BoxLocation(1, 0.1, 0.2, 0.9, 0.22), origin="ocr")
        reader = reader_text([text_layer, ocr])
        both, first = find_quote(reader, "Net Amount"), find_quote(reader, "Net")
        assert both is not None and first is not None
        self.assertEqual((both.origin, first.origin), ("ocr", "text_layer"))
        plain = find_quote(reader_text([line("Net")]), "Net")
        assert plain is not None
        self.assertIsNone(plain.origin)

    def test_ocr_table_cells_of_one_row_are_joined_with_a_bar(self) -> None:
        box = BoxLocation(1, 0.1, 0.3, 0.9, 0.6)
        cells = [
            OcrSpan("Cotton", "Cotton", box, origin="ocr", table_id="t1", row=1),
            OcrSpan("12", "12", box, origin="ocr", table_id="t1", row=1),
            OcrSpan("Silk", "Silk", box, origin="ocr", table_id="t1", row=2),
        ]
        self.assertEqual(reader_text(cells).text, "Cotton | 12\nSilk")

    def test_the_norm_offsets_cover_the_normalised_quote(self) -> None:
        reader = reader_text([line("Invoice No: INV-17")])
        found = find_quote(reader, "No: inv-17")
        assert found is not None
        self.assertEqual(reader.norm[found.start : found.end], "no: inv-17")

    def test_a_match_starting_inside_an_expanded_cluster_is_refused(self) -> None:
        # ½ normalises to three characters, 1 FRACTION-SLASH 2, all from one reader character.
        reader = reader_text([line("Qty " + HALF + " kg")])
        self.assertEqual(only(reader, "1" + FRACTION_SLASH + "2"), HALF)
        self.assertIsNone(find_quote(reader, FRACTION_SLASH + "2"))
        self.assertIsNone(find_quote(reader, "1" + FRACTION_SLASH))
        dotted = reader_text([line("Name " + DOTTED_I + "lker")])
        self.assertIsNone(find_quote(dotted, "name i"))

    def test_the_search_stays_inside_start_and_end(self) -> None:
        # ML 6(e): a window's quotes are searched only inside [start, end).
        reader = reader_text([line("Total 100"), line("Total 200", y=0.5)])
        middle = reader.norm.find("total 200")
        self.assertEqual(middle, 10)
        self.assertIsNone(find_quote(reader, "Total 100", start=middle))
        self.assertIsNone(find_quote(reader, "Total 200", end=middle + 8))
        found = find_quote(reader, "Total 200", start=middle, end=middle + 9)
        assert found is not None
        self.assertEqual(found.location.page, 1)
        self.assertEqual(found.text, "Total 200")

    def test_a_quote_over_the_cap_is_dropped(self) -> None:
        # Security A7: MAX_QUOTE_CHARS, before any insert.
        self.assertEqual(MAX_QUOTE_CHARS, 300)
        word = "abcd " * 60  # 300 characters
        reader = reader_text([line(word.strip() + " e")])
        self.assertIsNotNone(find_quote(reader, word[:MAX_QUOTE_CHARS]))
        self.assertIsNone(find_quote(reader, word + "e"))

    def test_reader_slice_over_the_cap_is_dropped(self) -> None:
        # The quote is short, but the reader's text at the match is not (joiners inside).
        long_word = "a" + ZWJ * MAX_QUOTE_CHARS + "b"
        self.assertIsNone(find_quote(reader_text([line(long_word)]), "ab"))


class FindLabelledTest(unittest.TestCase):
    def test_a_value_that_appears_twice_the_first_after_the_label_wins(self) -> None:
        spans = [line("Order date 12/08/2026", y=0.1), line("Invoice date 12/08/2026", y=0.2)]
        label, value = find_labelled(reader_text(spans), "Invoice date", "12/08/2026")
        assert label is not None and value is not None
        self.assertEqual(label.text, "Invoice date")
        self.assertEqual(value.location, spans[1].location)

    def test_with_no_label_the_first_in_the_document_wins(self) -> None:
        spans = [line("Order date 12/08/2026", y=0.1), line("Invoice date 12/08/2026", y=0.2)]
        reader = reader_text(spans)
        label, value = find_labelled(reader, None, "12/08/2026")
        assert value is not None
        self.assertIsNone(label)
        self.assertEqual(value.location, spans[0].location)
        self.assertEqual(find_labelled(reader, "  ", "12/08/2026"), (None, value))

    def test_the_first_label_with_a_value_in_its_window_wins(self) -> None:
        filler = " ".join(["x"] * 200)  # 399 characters: the first label's window ends inside it
        spans = [line("Total"), line(filler, y=0.2), line("Total 500", y=0.3), line("Total 900", y=0.4)]
        label, value = find_labelled(reader_text(spans), "Total", "500")
        assert label is not None and value is not None
        self.assertEqual(label.location, spans[2].location)
        self.assertEqual(value.text, "500")

    def test_a_label_on_page_15_and_its_value_on_page_16(self) -> None:
        reader = reader_text(
            [
                line("Profit of the month Sept is", 15, 0.90),
                line("Page 15 of 20", 15, 0.97),
                line("$12,000", 16, 0.05),
            ]
        )
        label, value = find_labelled(reader, "Profit of the month Sept is", "$12,000")
        assert label is not None and value is not None
        self.assertEqual((label.location.page, value.location.page), (15, 16))
        self.assertEqual(value.text, "$12,000")

    def test_the_value_is_found_up_to_300_characters_after_its_label(self) -> None:
        self.assertEqual(LABEL_WINDOW, 300)
        for gap, found in ((LABEL_WINDOW, True), (LABEL_WINDOW + 1, False)):
            with self.subTest(gap=gap):
                reader = reader_text([line("Net Amount" + "." * gap + "1,180")])
                label, value = find_labelled(reader, "Net Amount", "1,180")
                self.assertEqual(value is not None, found)
                self.assertEqual(label is not None, found)

    def test_a_label_that_is_not_found_drops_the_item(self) -> None:
        reader = reader_text([line("Total 1,180")])
        self.assertEqual(find_labelled(reader, "Grand Total", "1,180"), (None, None))
        self.assertEqual(find_labelled(reader, "Total", "1,190"), (None, None))

    def test_labelled_search_stays_inside_start_and_end(self) -> None:
        reader = reader_text([line("Total 100"), line("Total 200", y=0.5)])
        middle = reader.norm.find("total 200")
        self.assertEqual(middle, 10)
        label, value = find_labelled(reader, "Total", "100", start=middle)
        self.assertEqual((label, value), (None, None))
        label, value = find_labelled(reader, "Total", "200", start=middle)
        assert value is not None
        self.assertEqual(value.text, "200")


class LoosePassTest(unittest.TestCase):
    """ML 9(c): only for Devanagari quotes, only when the exact pass fails."""

    def test_marathi_ocr_errors_are_found_and_the_reader_text_is_stored(self) -> None:
        cases = {
            "पाटील": "पाटिल",  # ी read as ि
            "कांबळे": "कांबले",  # ळ read as ल
            "सावंत": "सावत",  # the anusvara lost
            "हँस": "हस",  # the candrabindu lost
            "दुसऱ्या": "दुसर्या",  # ऱ read as र
            "भूमी": "भुमि",  # ू read as ु and ी as ि
        }
        for quote, printed in cases.items():
            with self.subTest(quote=quote):
                reader = reader_text([line("Name: " + printed + " Pune")])
                found = find_quote(reader, quote)
                assert found is not None
                self.assertEqual(found.text, printed)
                self.assertTrue(found.loose)

    def test_an_exact_match_is_not_loose(self) -> None:
        reader = reader_text([line("पाटिल पाटील")])
        found = find_quote(reader, "पाटील")
        assert found is not None
        self.assertEqual((found.text, found.loose), ("पाटील", False))

    def test_a_loose_match_still_needs_whole_words(self) -> None:
        reader = reader_text([line("पाटिलकर Pune")])
        self.assertIsNone(find_quote(reader, "पाटील"))

    def test_a_labelled_devanagari_value_is_found_loosely(self) -> None:
        reader = reader_text([line("मालक: पाटिल"), line("गाव: कोल्हापूर", y=0.2)])
        label, value = find_labelled(reader, "मालक:", "पाटील")
        assert label is not None and value is not None
        self.assertEqual(value.text, "पाटिल")
        self.assertTrue(value.loose)

    def test_quote_with_nukta_against_ocr_without_it_is_found_loosely_and_counted(self) -> None:
        """ML 9(c) drops the nukta in the loose pass (the lead's decision, PRD2-BUILD-LEDGER).
        A loose match is OCR Devanagari, so its value is needs review anyway."""
        counts = QuoteCounts()
        ocr = reader_text([line("जिला नाशिक")])
        found = counts.add(find_quote(ocr, "ज" + NUKTA + "िला"))
        assert found is not None
        self.assertEqual((found.text, found.loose), ("जिला", True))
        with_nukta = reader_text([line("ज" + NUKTA + "िला नाशिक")])
        found = counts.add(find_quote(with_nukta, "जिला"))
        assert found is not None
        self.assertEqual((found.text, found.loose), ("ज" + NUKTA + "िला", True))
        self.assertEqual((counts.returned, counts.dropped, counts.loose), (2, 0, 2))


class QuoteCountsTest(unittest.TestCase):
    def test_returned_dropped_loose_and_ambiguous(self) -> None:
        reader = reader_text([line("Total 1,180"), line("Paid 1,180", y=0.2), line("पाटिल", y=0.3)])
        counts = QuoteCounts()
        once = counts.add(find_quote(reader, "Total"))
        twice = counts.add(find_quote(reader, "1,180"))
        counts.add(find_quote(reader, "1,190"))
        counts.add(find_quote(reader, "पाटील"))
        assert once is not None and twice is not None
        self.assertEqual((once.ambiguous, twice.ambiguous), (False, True))
        self.assertEqual((counts.returned, counts.dropped, counts.loose, counts.ambiguous), (4, 1, 1, 1))

    def test_a_labelled_item_is_ambiguous_when_a_later_label_has_a_value_too(self) -> None:
        reader = reader_text([line("Total 500"), line("Total 500", y=0.2)])
        label, value = find_labelled(reader, "Total", "500")
        assert value is not None and label is not None
        self.assertTrue(value.ambiguous)
        unique = find_labelled(reader_text([line("Total 500"), line("Paid 500", y=0.2)]), "Total", "500")[1]
        assert unique is not None
        self.assertFalse(unique.ambiguous)


# The seeded round-trip property (test-engineer amendments 8 and 15, ML 9(a)).
_UNITS = [
    *"abcXYZ019",
    "क",
    "कि",
    "मी",
    "ला",
    "क्ष",
    "त्र",
    "र्मा",
    "ज" + NUKTA,
    "ज" + NUKTA + "ि",
    "शिं",
    "हँ",
    "दुः",
    "क्" + ZWJ + "ष",
    "न्" + ZWNJ + "ह",
    "१२",
    "७",
    FI,
    HALF,
    SHARP_S,
    DOTTED_I,
    "\N{FULLWIDTH LATIN CAPITAL LETTER A}",
    "\N{FULLWIDTH DIGIT ONE}",
    "\N{EN DASH}",
    "\N{RIGHT SINGLE QUOTATION MARK}",
]
_SEPARATORS = [" ", "  ", "\t", NBSP, ", ", " - ", "/", " " + NBSP + "\t"]
_CELL_SEPARATORS = [*_SEPARATORS, " | "]


def _words(rng: random.Random) -> list[str]:
    return ["".join(rng.choices(_UNITS, k=rng.randint(1, 3))) for _ in range(rng.randint(1, 4))]


def _span_text(rng: random.Random, words: list[str], separators: list[str]) -> tuple[str, list[int]]:
    """The span's text and where each word starts in it."""
    text, starts = "", []
    for i, word in enumerate(words):
        if i:
            text += rng.choice(separators)
        starts.append(len(text))
        text += word
    return text, starts


@dataclass(frozen=True, slots=True)
class _Case:
    spans: list[Span]
    text: str  # the reader text the test expects: its own joins
    quote: str  # a run of whole words, copied from the reader text with our joins
    expected: str  # the same run with each join as one space
    offset: int  # where the run starts in the reader text


def _case(rng: random.Random) -> _Case:
    """1 to 3 spans (cells of rows of two, or boxes) and a random run of whole words in them."""
    as_cells = rng.random() < 0.5
    texts: list[tuple[str, list[str], list[int]]] = []
    spans: list[Span] = []
    for i in range(rng.randint(1, 3)):
        words = _words(rng)
        text, starts = _span_text(rng, words, _CELL_SEPARATORS if as_cells else _SEPARATORS)
        location: Location = (
            CellLocation(1, 1 + i // 2, 1 + i % 2)
            if as_cells
            else BoxLocation(1, 0.1, 0.1 * i, 0.9, 0.1 * i + 0.05)
        )
        spans.append(Span(text, text, location))
        texts.append((text, words, starts))
    # Cells 1 and 2 share row 1 (" | "); every other neighbour is a new line.
    joins = [" | " if as_cells and i % 2 == 1 else "\n" for i in range(1, len(spans))]
    first = rng.randrange(len(spans))
    last = rng.randrange(first, len(spans))
    a = rng.randrange(len(texts[first][1]))
    b = rng.randrange(a if first == last else 0, len(texts[last][1]))
    begin = texts[first][2][a]
    finish = texts[last][2][b] + len(texts[last][1][b])
    if first == last:
        parts = [texts[first][0][begin:finish]]
    else:
        parts = [
            texts[first][0][begin:],
            *(texts[k][0] for k in range(first + 1, last)),
            texts[last][0][:finish],
        ]
    quote = parts[0] + "".join(joins[first + k] + part for k, part in enumerate(parts[1:]))
    offset = sum(len(texts[k][0]) + len(joins[k]) for k in range(first)) + begin
    reader = "".join(text + join for (text, _, _), join in zip(texts, [*joins, ""], strict=True))
    return _Case(spans, reader, quote, " ".join(parts), offset)


class RoundTripPropertyTest(unittest.TestCase):
    def test_every_normalised_substring_round_trips(self) -> None:
        rng = random.Random(20260930)  # noqa: S311 - a fixed seed makes test text, not a secret
        for number in range(2000):
            case = _case(rng)
            reader = reader_text(case.spans)
            where = f"case {number}: {case.quote!r}"
            self.assertEqual(reader.text, case.text, where)
            j0 = bisect_left(reader.starts, case.offset)
            here = find_quote(reader, case.quote, start=j0)
            anywhere = find_quote(reader, case.quote)
            assert here is not None and anywhere is not None, where
            self.assertEqual((here.start, here.text), (j0, case.expected), where)
            self.assertLessEqual(anywhere.start, j0, where)
            self.assertEqual(reader.norm[anywhere.start : anywhere.end], normalise_quote(case.quote), where)


if __name__ == "__main__":
    unittest.main()
