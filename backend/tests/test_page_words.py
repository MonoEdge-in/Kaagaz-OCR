"""Your words on the page (package H): which lines hold the search words, by the four
ways of kaagaz/search/page_words.py, and the words cleaned and bounded."""

import unittest
from typing import Any

from kaagaz.chunking.script import latin_spelling
from kaagaz.search.page_words import MAX_WORD_CHARS, MAX_WORDS, Matcher, clean_words, segments, word_regions


def box(page: int, top: float) -> dict[str, Any]:
    return {"kind": "box", "page": page, "box": [0.1, top, 0.9, top + 0.03]}


def lit(words: list[str], line: str) -> bool:
    return Matcher(clean_words(words)).holds(line, None)


class CleanWordsTest(unittest.TestCase):
    def test_at_most_eight_words_each_at_most_forty_characters(self) -> None:
        words = clean_words([f"word{n}" for n in range(12)] + ["x" * 90])
        self.assertEqual(len(words), MAX_WORDS)
        self.assertEqual(clean_words(["k" * 90]), ["k" * MAX_WORD_CHARS])

    def test_cleaned_casefolded_once_and_never_a_stop_word(self) -> None:
        self.assertEqual(
            clean_words(["Krishna", "KRISHNA", "the", "kiti", "a", "<b>Maval</b>", "INV-1042"]),
            ["krishna", "maval", "inv", "1042"],
        )


class MatcherTest(unittest.TestCase):
    def test_the_same_word_in_any_case(self) -> None:
        self.assertTrue(lit(["krishna"], "Buyer: KRISHNA Sohoni"))
        self.assertTrue(lit(["सोहोनी"], "खरेदीदार कृष्ण सोहोनी"))
        self.assertFalse(lit(["krishna"], "Seller: Nikhil Varde"))

    def test_the_same_latin_spelling_across_scripts(self) -> None:
        self.assertTrue(lit(["krishna"], "खरेदीदार कृष्ण सोहोनी"))
        self.assertTrue(lit(["कृष्ण"], "Buyer: Krishna Sohoni"))

    def test_the_same_sound(self) -> None:
        self.assertTrue(lit(["krishana"], "Buyer: Krishna Sohoni"))
        self.assertTrue(lit(["krishana"], "खरेदीदार कृष्ण सोहोनी"))
        self.assertTrue(lit(["bhogwatdar"], "भोगवटादार"))

    def test_a_near_spelling_as_search_allows_it(self) -> None:
        self.assertTrue(lit(["kirshna"], "Buyer: Krishna Sohoni"))
        self.assertTrue(lit(["sohonee"], "Buyer: Krishna Sohoni"))
        self.assertFalse(lit(["mohan"], "Buyer: Krishna Sohoni"))

    def test_a_number_only_as_written(self) -> None:
        self.assertTrue(lit(["1042"], "Invoice INV-1042"))
        self.assertFalse(lit(["1043"], "Invoice INV-1042"))


class SegmentsTest(unittest.TestCase):
    def test_lines_pair_with_their_regions(self) -> None:
        self.assertEqual(segments("Buyer\nSeller", 2), ["Buyer", "Seller"])

    def test_table_cells_pair_without_their_column_names(self) -> None:
        text = "Name: Krishna Sohoni | Village: Maval\nName: Nikhil Varde | Village: Pune"
        self.assertEqual(segments(text, 4), ["Krishna Sohoni", "Maval", "Nikhil Varde", "Pune"])

    def test_text_that_pairs_with_nothing_is_left_out(self) -> None:
        self.assertIsNone(segments("Buyer\nSeller", 3))


class WordRegionsTest(unittest.TestCase):
    def test_the_open_pages_lines_and_every_page_with_one(self) -> None:
        pieces = [
            (
                "Invoice\nBuyer: Krishna Sohoni\nSeller: Nikhil Varde",
                None,
                [box(1, 0.1), box(1, 0.2), box(1, 0.3)],
            ),
            ("Village Maval", None, [box(2, 0.1)]),
            ("खरेदीदार कृष्ण", latin_spelling("खरेदीदार कृष्ण"), [box(3, 0.4)]),
            ("Name: Krishna | Village: Pune", None, [box(4, 0.1), box(4, 0.5)]),
            ("Krishna\nunpaired", None, [box(5, 0.1)]),
        ]
        words = clean_words(["krishna", "maval"])
        self.assertEqual(word_regions(pieces, words, 1), ([box(1, 0.2)], [1, 2, 3, 4]))
        self.assertEqual(word_regions(pieces, words, 4), ([box(4, 0.1)], [1, 2, 3, 4]))
        self.assertEqual(word_regions(pieces, words, 6), ([], [1, 2, 3, 4]))
        self.assertEqual(word_regions(pieces, [], 1), ([], []))


if __name__ == "__main__":
    unittest.main()
