"""Hinglish questions in plain English words (kaagaz/answering/hinglish.py)."""

import pytest

from kaagaz.answering.hinglish import normalise


def test_the_launch_example() -> None:
    result = normalise("Kiska profit sabse jyada tha last month?")
    assert result.english == "whose profit highest last month"
    assert result.keywords == ["profit", "highest"]
    assert result.hinglish is True


@pytest.mark.parametrize(
    ("question", "english"),
    [
        ("kaun", "who"),
        ("kon", "who"),
        ("kisne", "who"),
        ("kiski", "whose"),
        ("kiske", "whose"),
        ("sabse", "most"),
        ("sab se", "most"),
        ("jyada", "highest"),
        ("zyada", "highest"),
        ("jada", "highest"),
        ("adhik", "highest"),
        ("zyaada", "highest"),
        ("sabse zyada", "highest"),
        ("sab se jyada", "highest"),
        ("kam", "lowest"),
        ("sabse kam", "lowest"),
        ("kitna", "how much"),
        ("kitne", "how much"),
        ("kitni", "how much"),
        ("kul", "total"),
        ("jod", "total"),
        ("total", "total"),
        ("pichle mahine", "last month"),
        ("pichhle mahina", "last month"),
        ("pichla month", "last month"),
        ("last mahine", "last month"),
        ("is mahine", "this month"),
        ("pichle saal", "last year"),
        ("is varsh", "this year"),
        ("bill", "invoice"),
        ("bil", "invoice"),
        ("rakam", "amount"),
        ("paisa", "amount"),
        ("rashi", "amount"),
        ("amount", "amount"),
        ("munafa", "profit"),
        ("labh", "profit"),
        ("fayda", "profit"),
        ("bikri", "sales"),
        ("sale", "sales"),
        ("becha", "sales"),
        ("kharcha", "expense"),
        ("kharch", "expense"),
        ("kitne bill", "count invoices"),
        ("pichle 30 din", "last 30 days"),
    ],
)
def test_word_map(question: str, english: str) -> None:
    assert normalise(question).english == english


@pytest.mark.parametrize(
    "word", ["tha", "thi", "the", "hai", "hain", "ka", "ki", "ke", "me", "mein", "se", "ko", "kya"]
)
def test_stop_words_are_dropped(word: str) -> None:
    assert normalise(f"profit {word}").english == "profit"


@pytest.mark.parametrize("word", ["batao", "bataiye", "dikhao"])
def test_asking_words_are_dropped(word: str) -> None:
    assert normalise(f"kul bikri {word}").english == "total sales"


def test_hinglish_flag_is_false_for_an_english_question() -> None:
    result = normalise("What is the GSTIN of Sharma Traders?")
    assert result.hinglish is False
    assert result.english == "what is gstin of sharma traders"
    assert result.keywords == ["gstin", "sharma", "traders"]


def test_english_words_the_map_knows_do_not_make_it_hinglish() -> None:
    assert normalise("total bill amount last month").hinglish is False


def test_keywords_leave_out_date_words_years_and_day_counts() -> None:
    result = normalise("kul rakam pichle 30 din august 2026 mein")
    assert result.keywords == ["total", "amount"]


def test_document_numbers_stay_whole() -> None:
    result = normalise("INV-1042 ka total kya hai?")
    assert result.keywords == ["inv-1042", "total"]
    assert result.hinglish is True


def test_repeated_keywords_are_listed_once() -> None:
    assert normalise("profit profit munafa").keywords == ["profit"]


def test_punctuation_and_case_do_not_matter() -> None:
    assert normalise("KISKA   Profit, SABSE jyada??").english == "whose profit highest"


def test_an_empty_question_is_empty() -> None:
    result = normalise("?!")
    assert (result.english, result.keywords, result.hinglish) == ("", [], False)


def test_a_devanagari_question_is_spelled_in_latin_letters_not_dropped() -> None:
    """PRD 3 W3: before, every Devanagari letter was dropped, so the question had no keywords."""
    result = normalise("पाटील का बिल कितना है?")
    assert (result.english, result.keywords, result.hinglish) == (
        "patil invoice kitana",
        ["patil", "invoice", "kitana"],
        True,
    )
    assert normalise("INV-१०४२ ka total").keywords == ["inv-1042", "total"]  # Devanagari digits
