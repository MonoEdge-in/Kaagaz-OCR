"""The script of a text: how much is Devanagari (L2), whether OCR text looks
sound (ML 1(a)/(b), security A3), Devanagari digits (L4), and Latin spellings (L3).

Every expected value is a literal written by hand, never computed by the code
under test.
"""

import unittest

from kaagaz.chunking.piece import Piece
from kaagaz.chunking.script import (
    devanagari_share,
    fold_digits,
    has_devanagari_letters,
    is_devanagari,
    languages_without_model,
    latin_spelling,
    malformed_devanagari,
    ocr_script_suspect,
)
from kaagaz.reading.spans import BoxLocation

ZWJ, ZWNJ = "\u200d", "\u200c"
NUKTA = "\u093c"
ARABIC_YEH_WITH_HAMZA = "\u0626"


class DevanagariShareTest(unittest.TestCase):
    def test_just_under_half_is_not_devanagari_and_half_is(self) -> None:
        under = "क" * 49 + "a" * 51
        half = "क" * 50 + "a" * 50
        self.assertEqual(devanagari_share(under), 0.49)
        self.assertFalse(is_devanagari(under))
        self.assertEqual(devanagari_share(half), 0.5)
        self.assertTrue(is_devanagari(half))

    def test_vowel_signs_count_as_letters(self) -> None:
        # कि is a consonant and a vowel sign (category Mc): 2 Devanagari letters of 4.
        self.assertEqual(devanagari_share("कि ab"), 0.5)

    def test_digits_punctuation_and_spaces_are_not_letters(self) -> None:
        self.assertEqual(devanagari_share("राम 123, 456!"), 1.0)
        self.assertEqual(devanagari_share("Total १२३।"), 0.0)

    def test_text_with_no_letters_is_not_devanagari(self) -> None:
        for text in ("", "   ", "७/१२", "12,000.50", "।"):
            with self.subTest(text=text):
                self.assertEqual(devanagari_share(text), 0.0)
                self.assertFalse(is_devanagari(text))


class MalformedDevanagariTest(unittest.TestCase):
    def test_well_formed_words_have_no_bad_signs(self) -> None:
        for text in ("पाटील", "शिंदे", "कि", "क्", "क़ि", "कं", "क्" + ZWJ + "ष", "दुःख", "Invoice 17", ""):
            with self.subTest(text=text):
                self.assertEqual(malformed_devanagari(text), 0)

    def test_signs_that_do_not_follow_a_consonant_or_nukta_are_counted(self) -> None:
        cases = {
            "उता्याची": 1,  # a virama after the vowel sign ा (ML 1)
            "ाक": 1,  # a vowel sign with nothing before it
            "किी": 1,  # two vowel signs in a row
            "अा": 1,  # a vowel sign after an independent vowel
            "क ि": 1,  # a vowel sign after a space
            "क" + ZWJ + "ि": 1,  # a vowel sign after a joiner, not a consonant
            "्ाि": 3,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(malformed_devanagari(text), expected)


class OcrScriptTest(unittest.TestCase):
    def test_ascii_and_sound_devanagari_are_not_suspect(self) -> None:
        for text in ("Invoice No: INV-17", "पाटील ट्रेडर्स", "₹ १,२३,४५६.७८", "क्षेत्र ७/१२।", "Rs. 1,180/-", ""):
            with self.subTest(text=text):
                self.assertFalse(ocr_script_suspect(text))

    def test_a_letter_or_mark_from_another_script_is_suspect(self) -> None:
        cases = [
            "पाट" + ARABIC_YEH_WITH_HAMZA + "ल",  # Arabic yeh with hamza, as FINDINGS saw in Marathi OCR
            "\u0998\u09ae",  # Bengali letters
            "Caf\u00e9",  # a Latin letter outside ASCII (ML 1(a) allows ASCII only)
            "Cafe\u0301",  # a combining accent is a mark outside Devanagari
            "\uff21BC",  # a full-width letter
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(ocr_script_suspect(text))

    def test_malformed_devanagari_is_suspect(self) -> None:
        self.assertTrue(ocr_script_suspect("उता्याची"))

    def test_devanagari_letters_are_found_but_digits_and_danda_are_not(self) -> None:
        for text in ("पाटील", "Name: शिंदे", "कि", NUKTA):
            with self.subTest(text=text):
                self.assertTrue(has_devanagari_letters(text))
        for text in ("७/१२", "।", "॥", "₹ १,२३,४५६.७८", "Rs. 100", ""):
            with self.subTest(text=text):
                self.assertFalse(has_devanagari_letters(text))


class FoldDigitsTest(unittest.TestCase):
    def test_devanagari_digits_become_ascii_and_nothing_else_changes(self) -> None:
        cases = {
            "१२/०८/२०२६": "12/08/2026",
            "₹ १,२३,४५६.७८": "₹ 1,23,456.78",
            "०१२३४५६७८९": "0123456789",
            "पाटील 12": "पाटील 12",
            "Total ५": "Total 5",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fold_digits(text), expected)

    def test_the_length_never_changes(self) -> None:
        text = "क्षेत्र ७/१२ दिनांक"
        self.assertEqual(len(fold_digits(text)), len(text))


class LatinSpellingTest(unittest.TestCase):
    def test_the_ml_8_table(self) -> None:
        cases = {
            "शर्मा": "sharma",
            "गुप्ता": "gupta",
            "मिश्रा": "mishra",
            "सावंत": "savant",
            "कांबळे": "kambale",
            "चव्हाण": "chavhan",
            "राजेंद्र": "rajendra",
            "कृष्ण": "krishna",
            "हँस": "hans",
            "दुःख": "dukh",
            "मुंबई": "mumbai",
            "पाटील": "patil",
            "पाटिल": "patil",
            "पाटीळ": "patil",
            "शिंदे": "shinde",
            "७/१२": "7/12",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(latin_spelling(text), expected)

    def test_the_prd_acceptance_words(self) -> None:
        self.assertEqual(latin_spelling("राम"), "ram")
        self.assertEqual(latin_spelling("पाटील"), "patil")
        self.assertEqual(latin_spelling("शिंदे"), "shinde")

    def test_a_final_long_a_is_kept(self) -> None:
        self.assertEqual(latin_spelling("सातबारा"), "satabara")

    def test_cha_and_chha_stay_apart(self) -> None:
        self.assertEqual(latin_spelling("चाचा"), "chacha")
        self.assertEqual(latin_spelling("छाया"), "chhaya")

    def test_each_word_drops_its_own_final_a(self) -> None:
        self.assertEqual(latin_spelling("राम शिंदे"), "ram shinde")

    def test_a_final_consonant_after_a_virama_keeps_its_a(self) -> None:
        # The conjunct is kept as written, even with a joiner inside it.
        self.assertEqual(latin_spelling("क्" + ZWJ + "ष"), "ksha")

    def test_a_nukta_consonant_at_the_end_drops_its_a(self) -> None:
        self.assertEqual(latin_spelling("सफ़"), "saf")

    def test_mixed_text_keeps_its_latin_lower_cased(self) -> None:
        self.assertEqual(latin_spelling("Invoice INV-17 पाटील"), "invoice inv-17 patil")
        self.assertEqual(latin_spelling("Shinde & Co."), "shinde & co.")

    def test_joiners_are_dropped(self) -> None:
        self.assertEqual(latin_spelling("पाटील" + ZWNJ), "patil")

    def test_empty_text(self) -> None:
        self.assertEqual(latin_spelling(""), "")

    def test_the_danda_and_double_danda_become_full_stops(self) -> None:
        self.assertEqual(latin_spelling("राम।"), "ram.")
        self.assertEqual(latin_spelling("राम॥ शिंदे"), "ram. shinde")

    def test_candra_vowels_are_spelled_and_the_abbreviation_sign_dropped(self) -> None:
        # PROVISIONAL: IAST has no letter for these, so they leaked into the Latin text.
        cases = {
            "कॉपी": "kopi",  # U+0949, the candra o sign
            "बॅट": "bet",  # U+0945, the candra e sign
            "ऑफिस": "ophis",  # U+0911, candra o
            "ॲप": "ap",  # U+0972, candra a (Marathi)
            "ऍ": "e",  # U+090D, candra e
            "डॉ॰ शिंदे": "do shinde",  # U+0970, the abbreviation sign, is dropped
        }
        for word, latin in cases.items():
            with self.subTest(word=word):
                self.assertEqual(latin_spelling(word), latin)


def piece(text: str) -> Piece:
    return Piece("cust-a", "doc-1", text, (BoxLocation(1, 0.1, 0.1, 0.9, 0.12),), "verified")


class LanguagesWithoutModelTest(unittest.TestCase):
    """L2: with no model, Latin text is "en"; Hindi and Marathi cannot be told apart, so "other"."""

    def test_latin_pieces_are_en_and_devanagari_pieces_other(self) -> None:
        self.assertEqual(languages_without_model([piece("Total 1,180.00")]), ("en",))
        self.assertEqual(languages_without_model([piece("पाटील ट्रेडर्स")]), ("other",))

    def test_both_are_listed_once_en_first(self) -> None:
        pieces = [piece("शिंदे"), piece("Invoice"), piece("पाटील"), piece("Total")]
        self.assertEqual(languages_without_model(pieces), ("en", "other"))

    def test_each_piece_counts_by_its_own_share(self) -> None:
        # One piece, mostly Devanagari, with a Latin word inside: only "other".
        self.assertEqual(languages_without_model([piece("पाटील ट्रेडर्स INV")]), ("other",))

    def test_another_script_is_other_and_no_letters_is_nothing(self) -> None:
        self.assertEqual(languages_without_model([piece("محمد")]), ("other",))  # Arabic
        self.assertEqual(languages_without_model([piece("1,180.00 | 12/08/2026")]), ())
        self.assertEqual(languages_without_model([]), ())


if __name__ == "__main__":
    unittest.main()
