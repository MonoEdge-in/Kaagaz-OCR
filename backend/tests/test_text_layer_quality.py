"""R2: is a PDF page's text layer good enough, or does it go to OCR?

kaagaz/reading/text_quality.py, on strings and plain signals. The same checks
on real PDFs built in code are in tests/test_pdf_signals.py.
"""

import unittest

from kaagaz.reading.route import Reason, RouteDecision
from kaagaz.reading.text_quality import (
    HIDDEN_TEXT_MAX_SHARE,
    LEGACY_FONT_MAX_SHARE,
    MALFORMED_DEVANAGARI_MAX_SHARE,
    MAP_ERROR_MAX_SHARE,
    SCAN_FOOTER_MAX_CHARS,
    PageSignals,
    TextLayerRules,
    count_malformed_devanagari,
    good_ratio,
    malformed_devanagari_share,
    text_layer_fallback_allowed,
    text_layer_ok,
)

RULES = TextLayerRules(min_chars=40, min_good_ratio=0.9, scan_image_coverage=0.5)  # the PRD's values
PLAIN = PageSignals(coverage=0.0, hidden_share=0.0, legacy_font_share=0.0, map_error_share=0.0)
OK = RouteDecision("text_layer", "ok")

# Made-up page text.
ENGLISH = (
    "Tax Invoice No: INV-1042 Date: 12/08/2026 Bill to: Sharma Traders, Pune. "
    "Cotton fabric 40 m @ Rs. 250.00 = Rs. 10,000.00; CGST 2.5% - Rs. 250.00. Total due: Rs. 10,500.00"
)
HINDI = "भारत सरकार कार्यालय, पुणे जिला। दिनांक ७/१२/२०२६ क्षेत्र का विवरण: पाटील ट्रेडर्स, शिंदे मार्ग। कुल राशि ₹ १०,५००"


def signals(**shares: float) -> PageSignals:
    values = {"coverage": 0.0, "hidden_share": 0.0, "legacy_font_share": 0.0, "map_error_share": 0.0}
    return PageSignals(**(values | shares))


def check(text: str, page: PageSignals = PLAIN) -> RouteDecision:
    return text_layer_ok(text, page, RULES)


def ocr(reason: Reason) -> RouteDecision:
    return RouteDecision("ocr", reason)


class CleanTextTest(unittest.TestCase):
    def test_clean_english_stays_on_the_text_layer(self) -> None:
        self.assertEqual(check(ENGLISH), OK)
        self.assertEqual(good_ratio(ENGLISH), 1.0)

    def test_hindi_and_marathi_in_unicode_stay_on_the_text_layer(self) -> None:
        self.assertEqual(check(HINDI), OK)
        self.assertEqual(
            check("महाराष्ट्र शासन, सातबारा उतारा: गाव शिरूर, तालुका हवेली, जिल्हा पुणे। खातेदाराचे नाव व क्षेत्र"), OK
        )

    def test_the_rupee_sign_dashes_quotes_bullets_and_ellipsis_are_good(self) -> None:
        self.assertEqual(good_ratio("₹ \u2013 \u2014 \u2018 \u2019 \u201c \u201d \u2022 \u2026"), 1.0)


class FewCharactersTest(unittest.TestCase):
    def test_39_letters_and_digits_go_to_ocr_and_40_stay(self) -> None:
        self.assertEqual(check("Total " * 6 + "Rs 500"), ocr("few_chars"))  # 30 + 5 = 35
        self.assertEqual(check("abcde " * 7 + "abcd"), ocr("few_chars"))  # 39
        self.assertEqual(check("abcde " * 8), OK)  # 40

    def test_an_empty_text_layer_goes_to_ocr(self) -> None:
        self.assertEqual(check(""), ocr("few_chars"))
        self.assertEqual(check("  \n\t "), ocr("few_chars"))
        self.assertEqual(good_ratio(""), 0.0)

    def test_marks_and_punctuation_are_not_letters(self) -> None:
        # Devanagari vowel signs are marks, not letters: 20 letters and 20 signs is 20.
        self.assertEqual(check("का" * 20), ocr("few_chars"))
        self.assertEqual(check("---- .... ,,,, " * 10), ocr("few_chars"))


class BadCharactersTest(unittest.TestCase):
    def test_each_kind_of_bad_character_sends_the_page_to_ocr(self) -> None:
        for name, bad in {
            "replacement character": "\ufffd",
            "private use": "\ue000",
            "private use, last": "\uf8ff",
            "control character": "\x07",
            "unit separator (a control that isspace() calls space)": "\x1f",
            "Latin-1 symbol": "\u00a2",
            "Latin-1 letter": "\u00d6",
            "Latin-1 last": "\u00ff",
        }.items():
            with self.subTest(name):
                self.assertEqual(check("Invoice total 500 " * 5 + bad * 10), ocr("bad_chars"))

    def test_cid_sequences_count_as_bad_and_not_as_letters(self) -> None:
        self.assertEqual(check("Invoice total 500 " * 3 + "(cid:12)" * 20), ocr("bad_chars"))
        self.assertEqual(check("(cid:123)" * 30), ocr("few_chars"))  # no real letters at all
        self.assertEqual(good_ratio("(cid:3)(cid:45)"), 0.0)
        self.assertEqual(good_ratio("cid 12 (cid) cid:12"), 1.0)  # only the whole pattern is bad

    def test_an_old_devanagari_font_read_as_latin_1_symbols_is_bad(self) -> None:
        # Such fonts store Devanagari letters on Latin-1 codes (PRD R2's last rule).
        self.assertEqual(check("Hkkjr ljdkj ¼izFke½ ÿ ÷ Ö É ç Ã ¢ ¡ " * 3), ocr("bad_chars"))

    def test_the_ratio_is_over_non_whitespace_characters(self) -> None:
        text = "a" * 90 + "\ufffd" * 10
        self.assertEqual(good_ratio(text), 0.9)
        self.assertEqual(good_ratio(" \n ".join(text)), 0.9)  # spaces never dilute the bad ones
        self.assertEqual(good_ratio("a\u00a0" * 50), 1.0)  # a no-break space is a space, not Latin-1
        self.assertEqual(good_ratio("a\t\n" * 50), 1.0)

    def test_exactly_at_the_good_ratio(self) -> None:
        self.assertEqual(check("a" * 90 + "\ufffd" * 10), OK)  # 0.9 is not under 0.9
        self.assertEqual(check("a" * 89 + "\ufffd" * 11), ocr("bad_chars"))

    def test_other_scripts_are_neither_good_nor_bad_but_count(self) -> None:
        self.assertEqual(good_ratio("abcdefghi" + "ş"), 0.9)
        self.assertEqual(check("Invoice total 500 " * 3 + "Ελληνικά κείμενα εδώ"), ocr("bad_chars"))


class ScanImageTest(unittest.TestCase):
    def test_a_big_picture_with_a_short_text_goes_to_ocr(self) -> None:
        footer = ("Scanned by CamScanner, page 1 of 1. " * 10)[:150]
        self.assertEqual(check(footer, signals(coverage=0.6)), ocr("scan_image"))
        self.assertEqual(check(footer), OK)  # the same text with no picture

    def test_a_big_picture_with_enough_text_stays(self) -> None:
        self.assertEqual(check(("Invoice total 500 " * 20)[:250], signals(coverage=0.6)), OK)

    def test_exactly_at_the_coverage_and_the_length(self) -> None:
        self.assertEqual(SCAN_FOOTER_MAX_CHARS, 200)
        short = ("Invoice total 500 " * 20)[:150]
        self.assertEqual(check(short, signals(coverage=0.5)), OK)  # "more than" half
        self.assertEqual(check(short, signals(coverage=0.500001)), ocr("scan_image"))
        text_199, text_200 = ("Invoice total 500 " * 20)[:199], ("Invoice total 500 " * 20)[:200]
        self.assertEqual(check(text_199, signals(coverage=1.0)), ocr("scan_image"))
        self.assertEqual(check(text_200, signals(coverage=1.0)), OK)  # "fewer than 200"
        self.assertEqual(
            check("  " + text_199 + "\n\n", signals(coverage=1.0)), ocr("scan_image")
        )  # stripped


class FileSignalsTest(unittest.TestCase):
    def test_hidden_text_at_or_over_the_share_goes_to_ocr(self) -> None:
        self.assertEqual(HIDDEN_TEXT_MAX_SHARE, 0.05)
        self.assertEqual(check(ENGLISH, signals(hidden_share=0.05)), ocr("hidden_text"))
        self.assertEqual(check(ENGLISH, signals(hidden_share=0.0499)), OK)

    def test_an_old_devanagari_font_at_or_over_the_share_goes_to_ocr(self) -> None:
        self.assertEqual(LEGACY_FONT_MAX_SHARE, 0.2)
        self.assertEqual(check(ENGLISH, signals(legacy_font_share=0.2)), ocr("legacy_font"))
        self.assertEqual(check(ENGLISH, signals(legacy_font_share=0.199)), OK)

    def test_legacy_font_text_is_plain_ascii_so_only_the_font_name_catches_it(self) -> None:
        # "भारत सरकार कार्यालय" typed in Kruti Dev: every character is a good one.
        kruti = "Hkkjr ljdkj dk;kZy; iq.ks ftyk fnukad ls izkIr vkosnu"
        self.assertEqual(check(kruti), OK)
        self.assertEqual(check(kruti, signals(legacy_font_share=1.0)), ocr("legacy_font"))
        self.assertEqual(check("Hkkjr ljdkj dk;kZy;", signals(legacy_font_share=1.0)), ocr("legacy_font"))

    def test_unicode_map_errors_at_or_over_the_share_go_to_ocr(self) -> None:
        self.assertEqual(MAP_ERROR_MAX_SHARE, 0.05)
        self.assertEqual(check(ENGLISH, signals(map_error_share=0.05)), ocr("map_errors"))
        self.assertEqual(check(ENGLISH, signals(map_error_share=0.0499)), OK)

    def test_hidden_text_wins_over_every_other_reason(self) -> None:
        # The reason decides the fallback: hidden text is never kept (ML 4).
        everything = signals(coverage=1.0, hidden_share=1.0, legacy_font_share=1.0, map_error_share=1.0)
        self.assertEqual(check("", everything), ocr("hidden_text"))
        self.assertEqual(check("\ufffd" * 50, signals(hidden_share=0.5)), ocr("hidden_text"))

    def test_the_reasons_come_in_a_fixed_order(self) -> None:
        self.assertEqual(check("", signals(legacy_font_share=1.0, map_error_share=1.0)), ocr("legacy_font"))
        self.assertEqual(check("", signals(map_error_share=1.0)), ocr("map_errors"))
        self.assertEqual(check("\ufffd" * 10, signals(coverage=1.0)), ocr("few_chars"))
        self.assertEqual(check("a" * 50 + "\ufffd" * 50, signals(coverage=1.0)), ocr("bad_chars"))

    def test_only_hidden_text_is_never_the_fallback(self) -> None:
        self.assertFalse(text_layer_fallback_allowed(ocr("hidden_text")))
        kept: tuple[Reason, ...] = (
            "few_chars",
            "bad_chars",
            "scan_image",
            "legacy_font",
            "map_errors",
            "malformed_devanagari",
        )
        for reason in kept:
            with self.subTest(reason=reason):
                self.assertTrue(text_layer_fallback_allowed(ocr(reason)))
        self.assertTrue(text_layer_fallback_allowed(OK))


class MalformedDevanagariTest(unittest.TestCase):
    def test_well_formed_words_have_no_malformed_signs(self) -> None:
        for word in ("पाटील ट्रेडर्स", "शिंदे", "क्षेत्र ७/१२ दिनांक", "ज़िला", "कार्यालय", "हँस", "दुःख", HINDI):
            with self.subTest(word=word):
                self.assertEqual(count_malformed_devanagari(word), 0)

    def test_a_sign_that_follows_no_consonant_or_nukta_is_malformed(self) -> None:
        for text, count in {
            "उता्याची": 1,  # a virama after a vowel sign (ML 1)
            "िकताब": 1,  # visual order: the i sign typed before its consonant
            "काे": 1,  # two vowel signs in a row
            "अ्": 1,  # a virama after an independent vowel
            "कंा": 1,  # a vowel sign after the anusvara
            "ा ि ्": 3,  # signs after spaces and at the start
            "अॅडव्होकेट": 1,  # Marathi "ॲ" typed as अ + ॅ counts too
        }.items():
            with self.subTest(text=text):
                self.assertEqual(count_malformed_devanagari(text), count)

    def test_other_scripts_are_not_devanagari(self) -> None:
        self.assertEqual(count_malformed_devanagari("\u0626 Invoice"), 0)  # Arabic yeh with hamza
        self.assertEqual(malformed_devanagari_share("Invoice total"), 0.0)

    def test_the_share_is_over_devanagari_letters_and_signs(self) -> None:
        self.assertEqual(malformed_devanagari_share("क" * 98 + " ा ा"), 0.02)  # 2 of 100
        self.assertEqual(malformed_devanagari_share("Invoice ७/१२ । " + "क" * 98 + " ा ा"), 0.02)

    def test_over_the_share_goes_to_ocr_and_exactly_at_it_stays(self) -> None:
        self.assertEqual(MALFORMED_DEVANAGARI_MAX_SHARE, 0.02)
        self.assertEqual(check("क" * 98 + " ा ा"), OK)  # 0.02 is not over 0.02
        self.assertEqual(check("क" * 97 + " ा ा ा"), ocr("malformed_devanagari"))  # 0.03

    def test_a_text_layer_in_visual_order_goes_to_ocr(self) -> None:
        # Some old PDF makers store the i sign before its consonant, as it is drawn.
        logical = "किसान विकास पत्र योजना " * 4
        visual = "िकसान िवकास पत्र योजना " * 4
        self.assertEqual(check(logical), OK)
        self.assertEqual(check(visual), ocr("malformed_devanagari"))


if __name__ == "__main__":
    unittest.main()
