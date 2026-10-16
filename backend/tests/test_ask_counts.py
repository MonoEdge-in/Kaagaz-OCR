"""How many, or which, documents hold a word (Tushar, 16 Oct 2026): the question is routed
by a fixed word list, and answered from every document in scope, never the top hits. No
database: the service runs on the golden fakes and a fake store of documents; the SQL
itself is tests/test_ask_counts_db.py.
"""

import dataclasses
import uuid
from collections.abc import Sequence

import pytest

from kaagaz.answering.evidence import Hit, has_word, word_text
from kaagaz.answering.exhaustive import (
    MAX_LISTED,
    document_sentence,
    exhaustive_term,
    pages_with,
)
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.search.scope import Scope
from tests.test_answering_golden import (
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeHit,
    FakeRetriever,
    FakeUnchecked,
    piece,
)
from tests.test_word_review_text import BANNED

B = str(uuid.uuid4())


class FakeExhaustive:
    """``Exhaustive`` over in-memory pieces: a document counts when one of its pieces holds
    the phrase as whole words (the SQL's rule, ``has_word`` here), named in name order."""

    def __init__(self, pieces: dict[str, list[FakeHit]]) -> None:
        self.pieces = pieces
        self.calls: list[tuple[str, str, int]] = []

    def documents_with(
        self, customer_id: str, term: str, scope: Scope, listed: int
    ) -> tuple[int, list[list[Hit]]]:
        self.calls.append((customer_id, term, listed))
        found: dict[str, list[Hit]] = {}
        for hit in self.pieces.get(customer_id, []):
            if has_word(term.casefold(), word_text(hit.text)):
                found.setdefault(hit.document_id, []).append(hit)
        order = sorted(found, key=lambda d: (found[d][0].document_name.casefold(), d))
        return len(order), [found[d] for d in order[:listed]]


def assert_count_shape(answer: Answer) -> None:
    """The count sentence cites nothing; each document line cites its own one source, so no
    sentence carries more than one number; every source is numbered and can be pointed at."""
    data = answer.to_json()
    numbers = [citation["n"] for citation in data["citations"]]
    assert numbers == list(range(1, len(numbers) + 1))
    head, *lines = data["sentences"]
    assert head["citations"] == []
    assert [line["citations"] for line in lines] == [[n] for n in numbers]
    assert all(citation["regions"] for citation in data["citations"])


def doc(name: str, text: str, *, page: int = 1, label: str = "verified") -> FakeHit:
    hit = piece(name, text, label=label, regions=[{"kind": "box", "page": page, "box": [0.1, 0.2, 0.9, 0.3]}])
    return dataclasses.replace(hit, page_from=page, page_to=page)


def ask(
    question: str, pieces: list[FakeHit], *, other: list[FakeHit] | None = None
) -> tuple[Answer, FakeExhaustive]:
    store = FakeExhaustive({A: pieces, B: other or []})
    service = AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: pieces}),
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
        exhaustive=store,
    )
    return service.answer(A, question), store


# --- routing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("How many documents mention Jalna?", "Jalna"),
        ("Which documents contain the word Jalna?", "Jalna"),
        ("Which files contain Jalna", "Jalna"),
        ("List all files with Jalna road", "Jalna road"),
        ("Count documents containing Jalna", "Jalna"),
        ("Show all documents that mention Jalna", "Jalna"),
        ("In how many documents does Jalna appear?", "Jalna"),
        ("Which documents mention 'Jalna Road'?", "Jalna Road"),
        ("How many documents mention “Patil and Sons”?", "Patil and Sons"),
        ("Kitne documents me Jalna hai?", "Jalna"),
        ("Kaun se documents me Jalna likha hai", "Jalna"),
        ("Jalna kiti documents madhe ahe?", "Jalna"),
        ("Konte files madhe Jalna ahe?", "Jalna"),
    ],
)
def test_counting_and_listing_questions_are_routed_with_their_word(question: str, term: str) -> None:
    assert exhaustive_term(question) == term


@pytest.mark.parametrize(
    "question",
    [
        "What is the grand total of INV-1042?",
        "Who is the owner of the land in Jalna?",
        "How many documents do I have?",  # no word asked for
        "How many invoices last month?",  # the fixed count
        "Kitne bill is mahine?",
        "Which document has the highest amount?",  # an aggregate
        "Which documents have invoice number 1042?",  # words left on both sides: not one phrase
        "How many documents mention Jalna in 2024?",  # a date: not a word search
        "Which documents do not mention Jalna?",
        "Which documents mention Jalna or Pune?",
        "How many leaves do I get?",
        "What does the lease say about rent?",
    ],
)
def test_other_questions_are_not_routed(question: str) -> None:
    assert exhaustive_term(question) is None


# --- the answer ---------------------------------------------------------------------------


def jalna_store() -> list[FakeHit]:
    return [
        doc("sale-deed.pdf", "Village Jalna, taluka Ambad", page=2),
        doc("sale-deed.pdf", "Registered at JALNA office", page=5),
        doc("lease.pdf", "Shop at Jalna road", page=1),
        doc("bill-1042.pdf", "Sharma Traders, Pune"),  # not Jalna
        doc("notes.pdf", "Jalnawala sweets"),  # another word, never counted
    ]


def test_the_answer_states_the_exact_count_and_lists_every_document_with_its_pages() -> None:
    answer, store = ask("How many documents mention Jalna?", jalna_store())
    assert_count_shape(answer)
    data = answer.to_json()
    assert store.calls == [(A, "Jalna", MAX_LISTED)]
    assert (data["status"], data["route"], data["model_used"]) == ("answered", "fixed_query", False)
    assert [s["text"] for s in data["sentences"]] == [
        '2 documents mention "Jalna".',
        "lease.pdf: page 1.",
        "sale-deed.pdf: pages 2 and 5.",
    ]
    assert [c["document_name"] for c in data["citations"]] == ["lease.pdf", "sale-deed.pdf"]
    assert data["label"] == "verified"


def test_the_same_in_hinglish_and_marathi() -> None:
    for question in ("Kitne documents me Jalna hai?", "Jalna kiti documents madhe ahe?"):
        answer, _ = ask(question, jalna_store())
        assert answer.sentences[0].text == '2 documents mention "Jalna".', question


def test_past_the_cap_the_first_are_listed_and_the_rest_counted() -> None:
    pieces = [doc(f"file-{i:03}.pdf", f"Plot {i} in Jalna") for i in range(MAX_LISTED + 3)]
    answer, _ = ask("Which documents mention Jalna?", pieces)
    sentences = [s.text for s in answer.sentences]
    assert sentences[0] == '53 documents mention "Jalna". 50 are listed here; 3 more also mention it.'
    assert len(sentences) == 1 + MAX_LISTED
    assert sentences[1] == "file-000.pdf: page 1." and sentences[-1] == "file-049.pdf: page 1."
    assert len(answer.citations) == MAX_LISTED
    assert_count_shape(answer)


def test_one_document_reads_as_one() -> None:
    answer, _ = ask("Which file mentions Jalna?", [doc("lease.pdf", "Jalna road")])
    assert answer.sentences[0].text == '1 document mentions "Jalna".'


def test_no_document_holding_it_answers_as_before() -> None:
    answer, store = ask("How many documents mention Nagpur?", jalna_store())
    assert store.calls == [(A, "Nagpur", MAX_LISTED)]
    assert answer.route == "search"


def test_another_customers_documents_are_never_counted() -> None:
    answer, store = ask(
        "How many documents mention Jalna?", jalna_store(), other=[doc("theirs.pdf", "Jalna")]
    )
    assert {call[0] for call in store.calls} == {A}
    assert "theirs.pdf" not in str(answer.to_json())


def test_a_normal_question_never_asks_the_store() -> None:
    _, store = ask("What is the shop address on the lease?", jalna_store())
    assert store.calls == []


def test_a_needs_review_piece_makes_the_label_needs_review_with_calm_words() -> None:
    answer, _ = ask("Which documents mention Jalna?", [doc("lease.pdf", "Jalna road", label="needs_review")])
    assert answer.label == "needs_review"
    for sentence in answer.sentences:
        text = sentence.text.casefold()
        assert "review" not in text and "₹" not in text and "cost" not in text and "price" not in text
        assert not [word for word in BANNED if word in text]


def test_pages_of_a_piece_across_pages_are_its_lines_that_hold_the_word() -> None:
    across = dataclasses.replace(
        doc("deed.pdf", "Seller: Patil\nVillage: Jalna"),
        page_from=3,
        page_to=4,
        regions=[
            {"kind": "box", "page": 3, "box": [0, 0, 1, 0.1]},
            {"kind": "box", "page": 4, "box": [0, 0, 1, 0.1]},
        ],
    )
    assert pages_with([across], "Jalna") == [4]
    assert pages_with([dataclasses.replace(across, text="Jal-\nna")], "Jalna") == [3, 4]  # no one line: all


def test_document_sentence_names_a_few_pages_and_counts_many() -> None:
    pages: Sequence[int] = list(range(1, 15))
    assert document_sentence("a.pdf", [3]) == "a.pdf: page 3."
    assert document_sentence("a.pdf", [3, 7, 9]) == "a.pdf: pages 3, 7 and 9."
    assert document_sentence("a.pdf", pages) == "a.pdf: 14 pages."


# --- Devanagari (16 Oct 2026) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("कितने दस्तावेज़ों में जालना है?", "जालना"),
        ("कौन से दस्तावेज़ों में जालना लिखा है?", "जालना"),
        ("कौन-से दस्तावेज़ में जालना है", "जालना"),
        ("किन फ़ाइलों में जालना का नाम है", "जालना"),
        ("सभी दस्तावेज़ दिखाओ जिनमें जालना लिखा है", "जालना"),
        ("कितनी फाइलों में जालना रोड है?", "जालना रोड"),
        ('कितने दस्तावेज़ों में "जालना रोड" है?', "जालना रोड"),
        ("कितने दस्तावेज़ों में प्लॉट १२ है?", "प्लॉट 12"),  # Devanagari digits read as ASCII
        ("कितने documents में Jalna है?", "Jalna"),  # mixed scripts
        ("जालना किती कागदपत्रांमध्ये आहे?", "जालना"),  # Marathi: "in" joined to the noun
        ("कोणत्या फायलींमध्ये जालना आहे?", "जालना"),
        ("सर्व कागदपत्रे ज्यात जालना आहे", "जालना"),
        ("कोणत्या दस्तऐवजांमध्ये जालना असलेले नाव आहे?", "जालना"),
    ],
)
def test_devanagari_counting_questions_are_routed_with_their_word(question: str, term: str) -> None:
    assert exhaustive_term(question) == term


@pytest.mark.parametrize(
    "question",
    [
        "जालना के दस्तावेज़ में क्या लिखा है?",  # no asking word
        "जालना कुठे आहे?",
        "कितने दस्तावेज़ हैं?",  # no word asked for
        "सबसे ज़्यादा रकम वाला दस्तावेज़ कौन सा है?",  # an aggregate
        "किती कागदपत्रांमध्ये एकूण रक्कम आहे?",
        "किन दस्तावेज़ों में जालना नहीं है?",  # not: the other documents
        "कोणत्या कागदपत्रांमध्ये जालना नाही?",
        "सभी दस्तावेज़ों में रकम क्या है",  # asks what the amount is
        "जालना या पुणे किन दस्तावेज़ों में है?",  # two words, not one phrase
    ],
)
def test_other_devanagari_questions_are_not_routed(question: str) -> None:
    assert exhaustive_term(question) is None


def test_the_nukta_and_the_joiners_never_change_the_route() -> None:
    za_one, za_two = "ज़", "ज़"  # ज़ as one code point and as two
    fa_one, fa_two = "फ़", "फ़"  # फ़
    for things in (
        f"दस्तावे{za_one}ों",
        f"दस्तावे{za_two}ों",
        "दस्तावेजों",
        f"{fa_one}ाइलों",
        f"{fa_two}ाइलों",
        "फाइलों",
    ):
        assert exhaustive_term(f"कितने {things} में जालना है?") == "जालना", things
    assert exhaustive_term("कितने दस्ता‍वेजों में जालना है?") == "जालना"  # a ZWJ inside
    # The word asked for is kept as typed, its nukta too.
    assert exhaustive_term(f"कितने दस्तावेजों में {za_two}मीन है?") == f"{za_two}मीन"


def test_a_devanagari_word_is_also_searched_by_its_latin_spelling() -> None:
    from kaagaz.answering.exhaustive import latin_term

    assert latin_term("जालना") == "jalana"  # the stored text_latin recipe: middle vowels kept
    assert latin_term("जालना Road") == "jalana road"
    assert latin_term("Jalna") == "Jalna"  # a Latin word is searched as typed only


def test_a_devanagari_question_answers_in_the_same_calm_english() -> None:
    pieces = [
        doc("सातबारा.pdf", "गाव जालना, तालुका अंबड", page=2),
        doc("lease.pdf", "जालना रोड"),
        doc("bill.pdf", "पुणे"),
    ]
    answer, store = ask("कितने दस्तावेज़ों में जालना है?", pieces)
    assert store.calls == [(A, "जालना", MAX_LISTED)]
    assert [s.text for s in answer.sentences] == [
        '2 documents mention "जालना".',
        "lease.pdf: page 1.",
        "सातबारा.pdf: page 2.",
    ]
    assert_count_shape(answer)
