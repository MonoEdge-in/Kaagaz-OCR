"""The English to Marathi and Hindi glossary (kaagaz/answering/glossary.py)."""

import unicodedata
from datetime import date

import pytest

from kaagaz.answering import glossary
from kaagaz.answering.dates import date_range, mentions_date
from kaagaz.answering.hinglish import NormalisedQuestion, normalise
from kaagaz.answering.intent import FixedPlan, plan_fixed
from kaagaz.answering.rewrite import guard

QUESTIONS = (
    "Who is the owner of the land?",
    "Who owns gat number 48?",
    "Kiska profit sabse jyada tha last month?",
    "भोगवटादार कोण आहे?",
)


@pytest.mark.parametrize("keyword", sorted(glossary.EQUIVALENTS))
def test_every_term_is_pure_nfc_devanagari_letters_and_marks(keyword: str) -> None:
    """So no text but these constants ever reaches to_tsquery: no digit, danda, space,
    quote or operator (& | ! : * ( ) < - ' \\)."""
    terms = glossary.equivalents(keyword)
    assert 1 <= len(terms) <= 3 and len(set(terms)) == len(terms)
    assert keyword == normalise(keyword).keywords[0]  # written as normalise gives it
    for term in terms:
        assert unicodedata.is_normalized("NFC", term), term
        assert all("ऀ" <= ch <= "ॿ" and unicodedata.category(ch)[0] in "LM" for ch in term), term


def test_generic_words_are_listed_but_never_count_as_present() -> None:
    assert set(glossary.EQUIVALENTS) >= glossary.GENERIC
    assert not glossary.present("total", "एकूण रक्कम")
    assert not glossary.present("village", "गाव वडगाव")
    assert glossary.word_query(["total"]) == "एकूण:* | कुल | योग"


@pytest.mark.parametrize(
    ("keyword", "text", "expected"),
    [
        ("owner", "भोगवटादाराचे नाव", True),  # a long term starts a word
        ("owner", "A: जमीन मालकाचे नाव", True),
        ("tax", "कर भरला", True),  # a short term is a whole word
        ("tax", "करार", False),
        ("tax", "शेतकरी", False),
        ("profit", "लाभार्थी", False),
        ("land", "पुनर्जमीन", False),  # inside a word, not at its start
        ("land", "जमिनीचा", True),
        ("bank", "बैंक", True),
        ("unknown", "भोगवटादार", False),
    ],
)
def test_present_matches_whole_words_for_short_terms_and_word_starts_for_long_ones(
    keyword: str, text: str, expected: bool
) -> None:
    assert glossary.present(keyword, text) is expected


def test_kul_is_not_found_in_kulkarni(monkeypatch: pytest.MonkeyPatch) -> None:
    # कुल is "total"'s, a GENERIC word, so it is tried here under a word of its own.
    monkeypatch.setattr(glossary, "EQUIVALENTS", {"sum": ("कुल",)})
    assert not glossary.present("sum", "श्री कुलकर्णी")
    assert glossary.present("sum", "कुल 450")


def test_present_reads_both_nfc_and_decomposed_text() -> None:
    decomposed = unicodedata.normalize("NFD", "खरेदीदार")
    assert glossary.present("buyer", decomposed)


def test_word_query_ors_each_term_once_with_a_prefix_on_long_terms() -> None:
    assert glossary.word_query(["owner", "owns", "tax"]) == "भोगवटादार:* | मालक:* | खातेदार:* | कर | जीएसटी:*"
    assert glossary.word_query(["who", "inv-1042"]) == ""
    assert glossary.word_query([]) == ""


def _outputs(question: str) -> tuple[object, ...]:
    normalised = normalise(question)
    found = date_range(normalised, date(2026, 9, 27))
    fields = {"party_name", "profit", "date"}  # tests/test_answering_intent.py's REGISTER
    plan = plan_fixed(normalised, found, mentions_date(normalised), lambda _: fields)
    return normalised, plan, guard(question, question, ""), guard("the owner of the land", question, "")


def test_normalise_plan_fixed_and_the_guard_never_read_the_glossary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Byte-identical with the glossary emptied: they never expand a question."""
    with_glossary = [_outputs(question) for question in QUESTIONS]
    monkeypatch.setattr(glossary, "EQUIVALENTS", {})
    monkeypatch.setattr(glossary, "GENERIC", frozenset())

    assert [_outputs(question) for question in QUESTIONS] == with_glossary
    assert with_glossary == EXPECTED


# The outputs as main gives them (fix/s2-repair-main 342a935), written out.
EXPECTED = [
    (NormalisedQuestion("who is owner of land", ["owner", "land"], False), None, None, None),
    (
        NormalisedQuestion("who owns gat number 48", ["owns", "gat", "number", "48"], False),
        None,
        None,
        "rewrite_adds_facts",
    ),
    (
        NormalisedQuestion("whose profit highest last month", ["profit", "highest"], True),
        FixedPlan("highest", "profit", ("party_name",), ("date",), date(2026, 8, 1), date(2026, 9, 1)),
        None,
        "rewrite_adds_facts",
    ),
    (
        NormalisedQuestion("bhogavatadar who ahe", ["bhogavatadar", "ahe"], True),
        None,
        None,
        "rewrite_adds_facts",
    ),
]
