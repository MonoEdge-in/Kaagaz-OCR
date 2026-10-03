"""Marathi and Hindi typed in English letters (council 3 Oct 2026, PR 2b-2): the
Romanised glossary and its invariants, the stop words, the sound rule, the gate with
its traps, the cap, and the answer, with fakes for search and the model
(tests/test_answering_golden.py). The SQL is tested in test_cross_language_db.py.

The questions here are the council's development words; the eval
(tests/golden/questions_romanised.json) uses other spellings.
"""

import dataclasses
import typing
from collections.abc import Sequence

import pytest

from kaagaz.answering import glossary, hinglish, service
from kaagaz.answering.draft import DraftSentence
from kaagaz.answering.evidence import CAPPED, Gate, enough_evidence, number_evidence
from kaagaz.answering.hinglish import normalise
from kaagaz.chunking.script import SOUND_KEY_MIN, sound_key
from tests.test_answering_cross_language import AREA, GAT, LETTER, OWNER, ask, says
from tests.test_answering_golden import SETTINGS, FakeHit, piece


def gate(question: str, *hits: FakeHit) -> str | None:
    keywords = glossary.content_words(normalise(question).keywords)
    return enough_evidence(number_evidence(hits), keywords, SETTINGS.answer_min_evidence_score)


# --- the glossary's invariants ----------------------------------------------------------


def test_every_romanised_word_stands_for_an_english_key() -> None:
    assert set(glossary.ROMANISED.values()) <= set(glossary.EQUIVALENTS)
    assert len(glossary.ROMANISED) >= 40


def test_no_sound_key_stands_for_two_concepts() -> None:
    concepts: dict[str, set[str]] = {}
    for spelling, key in glossary._ROMANISED_SPELLINGS.items():
        concepts.setdefault(sound_key(spelling), set()).add(key)
    assert {key: found for key, found in concepts.items() if len(found) > 1} == {}


def test_every_romanised_key_is_long_enough_and_never_a_known_merge() -> None:
    assert all(len(key) >= SOUND_KEY_MIN for key in glossary.ROMANISED)
    # Only the three keys another common word shares are exact-only Romanised keys.
    assert set(glossary.ROMANISED) & glossary.MERGED_KEYS == {"bki", "bde", "brn"}
    assert [glossary.concept(word) for word in ("bakaya", "bhade", "bharna")] == ["due", "rent", "payment"]
    assert [glossary.concept(word) for word in ("baki", "bade", "baran", "barna")] == [None] * 4
    assert not {"kar", "kul", "varas", "rasid"} & set(glossary._ROMANISED_SPELLINGS)


def test_no_english_question_word_or_date_word_is_read_as_romanised() -> None:
    words = hinglish._NOT_KEYWORDS | hinglish.DATE_WORDS | hinglish._HINDI_STOP | hinglish._SHARED_STOP
    words |= set(glossary.EQUIVALENTS)
    # English words of the golden documents, among them silk (shillak's key).
    words |= {
        "silk",
        "cotton",
        "rayon",
        "print",
        "lining",
        "carrier",
        "client",
        "notice",
        "fee",
        "sugar",
        "oil",
    }
    assert {word: sound_key(word) for word in words if sound_key(word) in glossary.ROMANISED} == {}


def test_where_the_hinglish_map_and_the_glossary_overlap_they_agree() -> None:
    for word, english in hinglish._HINDI_WORDS.items():
        if sound_key(word) in glossary.ROMANISED and english in glossary.EQUIVALENTS:
            assert glossary.ROMANISED[sound_key(word)] == english, word


def test_a_keyword_is_looked_up_english_first_then_by_sound_then_by_its_start() -> None:
    assert [glossary.concept(word) for word in ("owner", "malak", "maalak", "zameen")] == [
        "owner",
        "owner",
        "owner",
        "land",
    ]
    assert glossary.concept("jaminicha") == "land"  # jminic starts with jamin's jmin
    assert glossary.concept("malakacha") is None  # mlk has 3 letters: never a start
    assert glossary.concept("gaikwad") is None


# --- the stop words, outside normalise ------------------------------------------------------


def test_stop_words_leave_the_keywords_but_normalise_is_unchanged() -> None:
    asked = normalise("malak kon ahe?")
    assert (asked.english, asked.keywords, asked.hinglish) == ("malak who ahe", ["malak", "ahe"], True)
    assert glossary.content_words(asked.keywords) == ["malak"]
    assert glossary.content_words(["pan", "check", "kar"]) == ["pan", "check"]  # a PAN is asked for


# --- the sound rule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "text", "heard"),
    [
        ("gaikwad", "गायकवाड सुनीता", True),  # equal keys
        ("jaminicha", "जमीन", True),  # the text's key starts the word's
        ("malak", "मालकाचे नाव", True),  # the word's key starts the text's
        ("mal", "मालकाचे", False),  # under 4 letters typed: whole keys only
        ("varsha", "वारस सीता", False),  # a known merge: the exact spelling only
        ("varas", "वारस सीता", True),
        ("rasid", "Received from Rashid Khan", False),
        ("rasid", "रसीद क्रमांक", True),
        ("khar", "कर ४५०", False),  # kr: under 3 letters, exact only
        ("rakkam", "रक्कम ४५०", False),  # amount is GENERIC
        ("48", "गट ४८", False),  # digits are has_token's
    ],
)
def test_a_keyword_sounds_like_a_word_only_by_the_rules(keyword: str, text: str, heard: bool) -> None:
    assert glossary.sounds_like(keyword, glossary.sound_words(text)) is heard


# --- the gate ------------------------------------------------------------------------------


def test_a_romanised_word_passes_through_the_glossary_and_a_name_by_sound() -> None:
    assert gate("malak kon ahe?", OWNER) == "glossary"
    assert gate("jaminicha malak kon?", OWNER) == "glossary"
    assert gate("kshetra kiti?", AREA) == "glossary"
    assert gate("gaikwad kon?", piece("satbara.csv", "A: नाव | B: गायकवाड सुनीता")) == "sound"


def test_the_typo_score_of_a_devanagari_piece_is_no_shortcut() -> None:
    """The bank letter trap: "malak ahe" against "... झाले आहे." is 0.40 by trigram."""
    assert gate("malak kon ahe?", dataclasses.replace(LETTER, fuzzy_score=0.9)) is None
    english = piece("letter.pdf", "Dear customer, your account is now open.")
    assert gate("malak kon ahe?", dataclasses.replace(english, fuzzy_score=0.9)) == "score"  # the control
    assert gate("malak kon ahe?", dataclasses.replace(LETTER, meaning_score=0.9)) == "score"


def test_the_numbers_asked_come_first_on_every_path() -> None:
    """gat 48 asked, only gat 47 there, even with a meaning score of 0.9."""
    assert gate("malak kon ahe gat 48", dataclasses.replace(OWNER, meaning_score=0.9), GAT) is None
    assert gate("malak kon ahe gat 47", OWNER, GAT) == "glossary"  # the control


@pytest.mark.parametrize(
    ("question", "text"),
    [
        ("khar kiti?", "कर ४५०"),
        ("check kar", "कर ४५०"),
        ("varsha kiti?", "वारस सीता"),
        ("rasid kuthe ahe?", "Received from Rashid Khan"),
    ],
)
def test_near_spellings_of_other_words_are_not_found(question: str, text: str) -> None:
    assert gate(question, piece("doc.pdf", text)) is None


def test_every_gate_value_is_capped_or_a_check_of_its_own() -> None:
    assert frozenset(typing.get_args(Gate)) == CAPPED | {"score", "keywords"}
    assert {"glossary", "sound", "personal"} == CAPPED


@pytest.mark.parametrize("passed", typing.get_args(Gate))
def test_a_capped_pass_is_never_verified(monkeypatch: pytest.MonkeyPatch, passed: Gate) -> None:
    def fixed(*_: object) -> Gate:
        return passed

    monkeypatch.setattr(service, "enough_evidence", fixed)
    answer = ask("What is the grand total?", [piece("bill.pdf", "Grand total: Rs. 450")])
    assert answer.status == "answered"
    assert answer.label == ("needs_review" if passed in CAPPED else "verified")


# --- the answer ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "quoted"),
    [
        ("malak kon ahe?", OWNER.text),
        ("jaminicha malak kon?", OWNER.text),
        ("kshetra kiti?", AREA.text),
    ],
)
def test_a_romanised_question_is_answered_and_needs_review(question: str, quoted: str) -> None:
    answer = ask(question, [GAT, OWNER, AREA])
    assert (answer.status, answer.label) == ("answered", "needs_review")
    assert [s.text for s in answer.sentences] == [quoted]


@pytest.mark.parametrize(
    ("question", "hits"),
    [
        ("malak kon ahe?", [dataclasses.replace(LETTER, fuzzy_score=0.4)]),
        ("malak kon ahe gat 48", [dataclasses.replace(OWNER, meaning_score=0.9), GAT]),
    ],
)
def test_the_traps_are_not_found(question: str, hits: Sequence[FakeHit]) -> None:
    assert ask(question, list(hits)).status == "not_found"


def test_a_romanised_answer_by_the_model_is_never_verified() -> None:
    model = says("रामचंद्र गणपत पाटील is the owner of the land.")
    answer = ask("jaminicha malak kon?", [OWNER], model)
    assert (answer.status, answer.label) == ("answered", "needs_review")


def test_a_latin_word_is_backed_by_sound_or_glossary_english_never_by_the_question_alone() -> None:
    texts = {1: OWNER.text}

    def unbacked(sentence: str) -> bool:
        return service._unbacked_latin(DraftSentence(sentence, [1]), texts, set())

    assert not unbacked("Ramchandra Ganpat Patil is the owner.")  # sounds like रामचंद्र गणपत पाटील
    assert not unbacked("The jamin owner is रामचंद्र गणपत पाटील.")  # jamin: land, and जमीन is cited
    assert unbacked("The malak is Sita Deshmukh.")  # nobody the evidence names
    assert unbacked("The kshetra is 1.20.")  # area: क्षेत्र is not in this cited text


# --- the code review's probes (PR 2b-2) -------------------------------------------------------

ROW = dataclasses.replace(
    piece("satbara.csv", "गट क्रमांक ४७ | भोगवटादार रामचंद्र गणपत पाटील"), meaning_score=0.9
)
FARM = dataclasses.replace(
    piece("satbara.csv", "गट क्रमांक ४७ | जमीन | शेतकरी | भोगवटादार रामचंद्र गणपत पाटील"), meaning_score=0.9
)


@pytest.mark.parametrize(
    ("question", "row", "reply", "label"),
    [
        ("Who owns gat 47?", ROW, "The भोगवटादार of gat 47 is Ramchandra Patil.", "verified"),  # the control
        ("Who owns gat 47?", ROW, "The भोगवटादार of gat 47 is Malik Patil.", "needs_review"),
        ("Who owns gat 47?", ROW, "The भोगवटादार of gat 47 is Malik.", "needs_review"),
        ("Who owns gat 47?", FARM, "The भोगवटादार of gat 47 is Kisan Patil.", "needs_review"),
        ("Who owns gat 47?", FARM, "The भोगवटादार of gat 47 is Bhumika Patil.", "needs_review"),
        ("Who owns gat 47?", FARM, "The भोगवटादार of gat 47 is Kishan Malik.", "needs_review"),
        ("gat 47 cha malak kon ahe?", ROW, "The भोगवटादार of gat 47 is Malik Patil.", "needs_review"),
        ("Who owns gat 47?", ROW, "The भोगवटादार of gat 47 is Ramchandran Patil.", "needs_review"),
    ],
)
def test_a_name_is_backed_by_an_equal_sound_never_by_a_glossary_meaning(
    question: str, row: FakeHit, reply: str, label: str
) -> None:
    answer = ask(question, [row], says(reply))
    assert (answer.status, answer.label) == ("answered", label)


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        ("gat 47 cha bhogvatdar kisan patil ahe ka?", "The भोगवटादार of gat 47 is kisan patil."),
        ("gat 47 cha bhogvatdar malik patil ahe ka?", "The भोगवटादार of gat 47 is malik patil."),
        ("gat 47 cha malak kon ahe?", "The भोगवटादार of gat 47 is malak patil."),
    ],
)
def test_a_romanised_word_of_the_question_echoed_in_lower_case_backs_nothing(
    question: str, reply: str
) -> None:
    answer = ask(question, [ROW], says(reply))
    assert (answer.status, answer.label) == ("answered", "needs_review")


@pytest.mark.parametrize(
    ("question", "text", "passed"),
    [
        ("rin kiti ahe?", "Printing charges: Rs 5,000", None),  # rin is not in printing
        ("ekar kiti ahe?", "Ekart courier charges Rs 120", "sound"),  # ekr starts ekrt: capped
    ],
)
def test_a_romanised_keyword_counts_as_a_whole_word_only(
    question: str, text: str, passed: str | None
) -> None:
    hit = piece("bill.pdf", text)
    assert gate(question, hit) == passed
    for answer in (ask(question, [hit]), ask(question, [hit], says(text))):
        assert answer.label != "verified"


def test_a_romanised_keyword_found_as_written_is_still_capped() -> None:
    assert gate("malak kon ahe?", piece("deed.pdf", "Malak: Ram Patil")) == "glossary"


@pytest.mark.parametrize(
    ("question", "text", "reply"),
    [
        (
            "Show the invoice for September",
            "Invoices for September: 3",
            "There are 3 invoices for September.",
        ),
        ("What is the total?", "GrandTotal 500", "The total is 500."),
        ("What is the GSTIN?", "GSTIN27XYZ", "The GSTIN is GSTIN27XYZ."),
    ],
)
def test_a_plural_camel_case_or_a_word_joined_to_digits_still_counts_as_written(
    question: str, text: str, reply: str
) -> None:
    hit = piece("bill.pdf", text)
    assert ask(question, [hit], says(reply)).label == "verified"
