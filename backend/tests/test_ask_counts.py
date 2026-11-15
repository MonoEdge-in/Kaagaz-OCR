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
    asks_documents,
    count_sentence,
    document_sentence,
    exhaustive_query,
    exhaustive_term,
    key_terms,
    mention_row,
    pages_with,
)
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.search.hybrid import MAX_LIMIT
from kaagaz.search.scope import Scope
from tests.test_answering_golden import (
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeHit,
    FakeModel,
    FakeRetriever,
    FakeUnchecked,
    evidence_blocks,
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
        self.scopes: list[Scope] = []

    def documents_with(
        self, customer_id: str, term: str, scope: Scope, listed: int
    ) -> tuple[int, list[list[Hit]]]:
        self.calls.append((customer_id, term, listed))
        self.scopes.append(scope)
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


# --- a document type before "documents of" (Tushar, 17 Oct 2026) ----------------------------


def test_type_words_before_documents_of_filter_by_type_and_route_with_no_model() -> None:
    assert exhaustive_query("how many land document of jalna i have") == ("jalna", ("land_record",))
    assert exhaustive_query("how many documents of jalna") == ("jalna", ())
    assert exhaustive_query("which bill documents of Patil") == ("Patil", ("invoice",))
    assert exhaustive_query("how many xyz documents of jalna") == ("jalna", ())  # unknown: no filter
    assert exhaustive_query("How many documents mention Jalna?") == ("Jalna", ())
    for question in ("Who is the owner of the land in Jalna?", "Which documents have invoice number 1042?"):
        assert exhaustive_query(question) is None


def test_the_type_narrows_the_scope_and_names_the_count() -> None:
    answer, store = ask("how many land document of jalna i have", jalna_store())
    assert store.calls == [(A, "jalna", MAX_LISTED)]
    assert store.scopes[0].doc_types == ("land_record",)
    assert answer.sentences[0].text == '2 land records mention "jalna".'
    assert count_sentence("Jalna", 1, ("land_record",)) == '1 land record mentions "Jalna".'
    assert count_sentence("Jalna", 3, ("land_record", "invoice")) == '3 documents mention "Jalna".'


# --- Also mentions (Tushar, 17 Oct 2026) ---------------------------------------------------


def test_the_not_found_line_never_names_a_request_word() -> None:
    # #622 (production, 15 Nov 2026): it said 'No document searched has the words "Can", "find"
    # or "measurement".'
    question = "Can you find me the measurement of this if it is seen in any other document?"
    assert key_terms(question) == ["measurement"]
    assert key_terms("which document have krishna jindal name") == ["krishna", "jindal"]


def test_key_terms_keep_names_and_places_and_drop_the_rest() -> None:
    assert key_terms("Who is the owner of the land in Jalna?") == ["Jalna"]
    assert key_terms("गाव दरेगांव जालना जिल्हा कोणता आहे?") == ["दरेगांव", "जालना"]
    assert key_terms("Jalna ka malak kaun hai") == ["Jalna"]  # malak is the glossary's owner
    assert key_terms("show me the 7/12 of Patil in Jalna district last month") == ["Patil", "Jalna"]
    # Words the cited text holds first; never more than three.
    assert key_terms("Patil Shinde Jadhav Jalna", ["Village Jalna"]) == ["Jalna", "Patil", "Shinde"]
    assert key_terms("How many invoices last month?") == []


def test_a_mentioned_row_is_two_clean_lines_from_the_first_match() -> None:
    row = mention_row(
        "d1", "<b>7-12.pdf</b>", [2, 5], 2, "Header\nगाव : दरेगांव\nजिल्हा : जालना\nend", ["जालना"]
    )
    assert row == {
        "document_id": "d1",
        "document_name": "7-12.pdf",
        "page_no": 2,
        "pages": [2, 5],
        "snippet": "जिल्हा : जालना\nend",
    }
    assert mention_row("d1", "a", [1], 1, "one\ntwo\nthree", ["Nagpur"])["snippet"] == "one\ntwo"


class FakeMentions:
    """``Mentions``: records each call and gives back what it was set up with."""

    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.calls: list[tuple[str, list[str], list[str]]] = []

    def mentions(
        self, customer_id: str, terms: Sequence[str], scope: Scope, exclude: Sequence[str], listed: int
    ) -> tuple[int, list[dict[str, object]]]:
        self.calls.append((customer_id, list(terms), list(exclude)))
        rows = [
            {"document_id": "m1", "document_name": "m1.pdf", "page_no": 1, "pages": [1], "snippet": "Jalna"}
        ]
        return self.total, rows if self.total else []


def mentioned(question: str, port: FakeMentions, store: FakeExhaustive | None = None) -> Answer:
    pieces = jalna_store()
    return AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: pieces}),
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
        exhaustive=store or FakeExhaustive({A: pieces}),
        mentions=port,
    ).answer(A, question)


def test_an_answer_lists_the_other_documents_with_its_key_terms_not_the_cited_ones() -> None:
    port = FakeMentions()
    answer = mentioned("Where is the shop on Jalna road?", port)
    assert answer.status in ("answered", "not_found")
    cited = sorted(
        {c.document_id for c in answer.citations}
        | {d["document_id"] for d in answer.related_documents}
        | {d["id"] for d in answer.closest_documents}
    )
    assert port.calls == [(A, ["shop", "Jalna", "road"], cited)]  # all in the cited text
    assert answer.mentions is not None
    assert answer.to_json()["mentions"] == {
        "terms": ["shop", "Jalna", "road"],
        "total": 3,
        "documents": answer.mentions["documents"],
    }


def test_a_not_found_answers_closest_documents_are_not_mentioned_again() -> None:
    # CodeRabbit (17 Oct): a not-found answer's "Closest documents" are already shown.
    port = FakeMentions()
    answer = mentioned("Where is the shop on Jalna road?", port)
    closest = dataclasses.replace(
        answer, status="not_found", closest_documents=[{"id": "c9", "display_name": "c9.pdf"}]
    )
    service = AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: jalna_store()}),
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
        exhaustive=FakeExhaustive({A: jalna_store()}),
        mentions=port,
    )
    port.calls.clear()
    service._mentioned(A, "Where is the shop on Jalna road?", closest, Scope())
    assert "c9" in port.calls[0][2]


def test_no_mentions_for_a_count_no_key_term_or_none_found() -> None:
    port = FakeMentions()
    assert mentioned("How many documents mention Jalna?", port).mentions is None  # it lists them itself
    assert mentioned("What is the total?", port).mentions is None  # no key term
    assert port.calls == []
    empty = FakeMentions(total=0)
    answer = mentioned("Where is the shop on Jalna road?", empty)
    assert empty.calls and answer.mentions is None and "mentions" not in answer.to_json()


# --- which documents, from the search (#617 item 5) -------------------------------------


@pytest.mark.parametrize(
    ("question", "asks"),
    [
        ("which document have krishna jindal name", True),
        ("Which files mention Gat 61?", True),
        ("How many documents have the name Patil?", True),
        ("list the papers of Wagholi", True),
        ("kaun se documents me Jindal ka naam hai", True),
        ("कोणत्या कागदपत्रात गट ६१ आहे", True),
        ("किन दस्तावेज़ों में जिंदल है", True),
        ("What is the invoice number in this document?", False),  # one value of one document
        ("all documents total", False),
        ("Which invoice is the largest?", False),  # no document word
        ("Who is the owner of Gat 61?", False),
    ],
)
def test_which_documents_questions_are_read_in_every_script(question: str, asks: bool) -> None:
    assert asks_documents(question) is asks


def test_a_which_documents_question_reads_each_document_once() -> None:
    """#617 item 5: the search keeps MAX_LIMIT hits and the draft reads each document's best
    one, so a document with many matching pieces no longer fills the evidence alone."""
    many = [piece("a.pdf", f"Krishna Jindal signed page {n}") for n in range(6)]
    pieces = [*many, piece("b.pdf", "Buyer: Krishna Jindal"), piece("c.pdf", "Witness: Krishna Jindal")]
    retriever = FakeRetriever({A: pieces})
    model = FakeModel(lambda system, user: {"status": "not_found", "sentences": []})
    service = AnswerService(
        db=UNUSED_DB,
        model=model,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=retriever,
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
    )

    service.answer(A, "which document have krishna jindal name")

    assert [call[3] for call in retriever.calls] == [MAX_LIMIT]
    drafted = [list(blocks.values()) for _, user in model.prompts if (blocks := evidence_blocks(user))]
    assert len(drafted[0]) == 3  # a.pdf's best piece, then b.pdf's and c.pdf's
    for hit in (pieces[0], pieces[6], pieces[7]):
        assert sum(hit.text in block for block in drafted[0]) == 1, hit.document_name

    service.answer(A, "Where did Krishna Jindal sign?")  # any other question: as before
    assert retriever.calls[-1][3] == SETTINGS.answer_max_evidence
