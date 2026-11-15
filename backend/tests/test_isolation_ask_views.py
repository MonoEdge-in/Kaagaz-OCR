"""PRD 7 PR 8 (#602): Ask and search read only what the person can see.

PR 6's walled organisation. Five of its files get the same content (``_ask_org``): fin, sal and
held are hidden from the plain reviewer, sales and given are not; Finance's member sees fin, held
and given but not sal. Each file has two pieces: the first holds a word of its own and "ledger",
with a vector of its own (one axis each); and a section, a topic, a card and the reviewer's own
story; a total, an invoice date, a seller, an unchecked card figure and an invoice row; a land
record (an owner named after it, a village of its own in Satara); an unreadable page and an OCR word
mark. Every Ask and search statement is called as the person, so it is proved by itself: nothing
of a hidden file, not as a row, a count, a weight or a slot before a LIMIT, and the member still
finds fin."""

import json
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Connection, event, text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.answering import exhaustive, fixed_queries, ready, rewrite, stream
from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, latin_key, picked_page
from kaagaz.answering.evidence import PgRetriever
from kaagaz.answering.exhaustive import PgExhaustive
from kaagaz.answering.expansion import NO_EXPANSION
from kaagaz.answering.fixed_queries import PgFixedQueries
from kaagaz.answering.hard_to_read import PgReadingQuality
from kaagaz.answering.memory import NO_MEMORY, VERSION, Memory
from kaagaz.answering.owners import OWNING, PgOwners
from kaagaz.answering.rewrite import Turn
from kaagaz.answering.service import AnswerService
from kaagaz.answering.unchecked import PgUncheckedValues
from kaagaz.api.routers.chat import CLARIFY_GONE, EARLIER_CHOICE
from kaagaz.api.views import NO_ACCESS, NO_ACCESS_LINE
from kaagaz.db import chat as chat_sql
from kaagaz.db import organization_words
from kaagaz.db.engine import Database
from kaagaz.db.models import Agent, ChatChoice
from kaagaz.search import hybrid
from kaagaz.search.hybrid import FusedPiece
from kaagaz.search.scope import EVERYTHING
from kaagaz.search.story_search import read_stories
from tests.db_support import insert_key, insert_tag, tag_document
from tests.settings_factory import make_settings
from tests.test_answering_golden import NOW, evidence_blocks
from tests.test_api_chat import _cited_answer
from tests.test_api_chat_clarify import _clarify
from tests.test_api_support import CSRF, Api, answer_json
from tests.test_ask_speed_db import Model
from tests.test_isolation_access_views import Org, _app, _walled
from tests.test_operator_auth_db import _owner
from tests.test_search_db import EF_SEARCH, RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

FILES = ("fin", "sal", "held", "sales", "given")
HIDDEN = {"plain": ("fin", "sal", "held"), "member": ("sal",)}
WORDS = {"fin": "aardvark", "sal": "badger", "held": "cheetah", "sales": "dingo", "given": "emu"}
_SECTION = text(
    "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
    " piece_seq_from, piece_seq_to, summary, tsv) VALUES (:c, :d, 0, '{Ledger}', 1, 1, 0, 1, :s,"
    " to_tsvector('simple', 'Ledger ' || :s))"
)
_TOPIC = text(
    "INSERT INTO document_topics (customer_id, document_id, topic, aliases, piece_seqs)"
    " VALUES (:c, :d, :t, '{}', '{0, 1}')"
)
_VALUE = (
    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text, value_raw,"
    " value_number, value_date, page_no, region, reader, label, label_reason)"
    ' VALUES (:c, :d, :key, :f, :t, :t, :n, :day, 1, \'{"kind": "box", "page": 1, "box": [0.1, 0.1,'
    " 0.2, 0.2]}', 'test', :label, 'readers_agree')"
)
AMOUNTS = {name: 7001 + n for n, name in enumerate(FILES)}


def _figures(api: Api, org: Org, name: str) -> None:
    """A total, an invoice date and a seller on the document record, an unchecked card figure,
    an invoice row, a land record keyed by #245, an unreadable page and an OCR word mark."""
    db, c, d, word = api.db, org.c, org.files[name], WORDS[name]
    rows = [
        ("document", "total_amount", str(AMOUNTS[name]), AMOUNTS[name], None, "verified"),
        ("document", "invoice_date", "2026-08-10", None, "2026-08-10", "verified"),
        ("document", "seller_name", f"Seller {word}", None, None, "verified"),
        ("card", "total_amount", str(AMOUNTS[name]), AMOUNTS[name], None, "needs_review"),
    ]
    if name == "fin":
        rows.append(("document", "gst_amount", "18", 18, None, "verified"))
    for key, field, said, number, day, label in rows:
        params = {"c": c, "d": d, "key": key, "f": field, "t": said, "n": number, "day": day, "label": label}
        _owner(db, _VALUE, params)
    _owner(
        db,
        "INSERT INTO invoices (customer_id, document_id, invoice_date, label, check_status)"
        " VALUES (:c, :d, '2026-08-10', 'verified', 'passed')",
        {"c": c, "d": d},
    )
    with db.customer(c) as conn:
        insert_key(conn, c, d, "person", f"Ramesh {word}")
        insert_key(conn, c, d, "place", f"{word}pur")
        insert_key(conn, c, d, "place", "Satara", "district")
    api.add_page(c, d)
    _owner(db, "UPDATE pages SET read_quality = 'unreadable' WHERE document_id = :d", {"d": d})
    mark = {"text": word, "raw": word, "origin": "ocr", "quality": "unsure"}
    api.add_reader_output(c, d, [{**mark, "region": {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]}}])


@dataclass
class AskOrg:
    api: Api
    db: Database
    org: Org
    pieces: dict[str, list[str]]  # file name: its two piece ids
    axes: dict[str, int]  # file name: the axis of its first piece's vector

    def id(self, name: str) -> str:
        return self.org.files[name]

    def user(self, name: str) -> str:
        return self.org.people[name].user_id


def _ask_org(api: Api) -> AskOrg:
    org = _walled(api)
    db, c = api.db, org.c
    pieces, axes = {}, {}
    for n, name in enumerate(FILES):
        d, word = org.files[name], WORDS[name]
        axes[name] = n
        pieces[name] = add_pieces(
            db, c, d, [(f"Invoice {word} ledger", axis(n)), (f"Ledger notes {name}", None)]
        )
        # fin's and sal's sections match "ledger" best, so a table read would give them s's 2 places.
        summary = f"{word} ledger ledger ledger" if name in ("fin", "sal") else f"{word} ledger"
        _owner(db, _SECTION.text, {"c": c, "d": d, "s": summary})
        _owner(db, _TOPIC.text, {"c": c, "d": d, "t": f"topic {word}"})
        _owner(db, 'UPDATE documents SET card = \'{"kind": "invoice"}\' WHERE id = :d', {"d": d})
        _owner(
            db,
            "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
            " VALUES (:c, :u, :d, :s, :s)",
            {"c": c, "u": org.people["plain"].user_id, "d": d, "s": f"my {word} story"},
        )
        _figures(api, org, name)
    return AskOrg(api, db, org, pieces, axes)


@pytest.fixture
def ask(db: Database) -> AskOrg:
    return _ask_org(Api(db))


@contextmanager
def _as(db: Database, ask: AskOrg, name: str) -> Iterator[Connection]:
    with _app(db).user(ask.org.c, ask.user(name)) as conn:
        yield conn


def _search(
    conn: Connection, ask: AskOrg, query: str, vector: list[float] | None = None, **walk: Any
) -> list[Any]:
    return hybrid.search(
        conn,
        customer_id=ask.org.c,
        query_text=query,
        embedding=vector,
        limit=50,
        recipe=RECIPE,
        ef_search=EF_SEARCH,
        **walk,
    )


def _list_rows(statement: Any, ask: AskOrg, conn: Connection, query: str) -> list[Any]:
    """A word or typo list's own documents: search() fetches details through the view again, so the
    list itself is checked here."""
    conn.execute(hybrid._SET_FUZZY_THRESHOLD, {"t": str(hybrid.FUZZY_THRESHOLD)})
    words = {"c": ask.org.c, "q": query, "k": query, "ql": query, "g": "", "sk": "", "n": 50}
    return [r[1] for r in conn.execute(statement, words | EVERYTHING.params())]


def _meaning_rows(statement: Any, ask: AskOrg) -> Callable[[Connection], list[Any]]:
    params = {"c": ask.org.c, "vec": hybrid.vector_literal(axis(ask.axes["fin"])), "recipe": RECIPE, "n": 50}
    return lambda conn: [r[1] for r in conn.execute(statement, params | EVERYTHING.params())]


def _hit(ask: AskOrg, name: str) -> Any:
    """A forged hit on ``name``'s first piece: what a caller could pass in from an older list."""
    rows = _owner(
        ask.db,
        "SELECT p.id, p.document_id, 'x', 'pdf', p.page_from, p.page_to, p.text, p.regions, p.label, NULL,"
        " p.seq, p.context_header FROM pieces p WHERE p.id = :p",
        {"p": ask.pieces[name][0]},
    )
    return hybrid._unseen(rows, [])[0]


def _documents(found: Any) -> set[str]:
    """The document ids of a statement's result: hits, rows (document id second) or ids."""
    out = set()
    for each in found:
        if isinstance(each, str):
            out.add(each)
        elif hasattr(each, "document_id"):
            out.add(str(each.document_id))
        elif hasattr(each, "best"):
            out.add(str(each.best.document_id))
        else:
            out.add(str(each))
    return out


# Each search statement, called as the person with fin's word, vector, topic or ids. Every one
# finds fin for Finance's member, and nothing hidden for the plain reviewer.
_Call = Callable[[Connection, AskOrg], Any]
_CALLS: list[tuple[str, _Call]] = [
    ("search, words", lambda conn, a: _search(conn, a, WORDS["fin"])),
    ("search, typos", lambda conn, a: _search(conn, a, "aardvrk")),
    ("_WORD", lambda conn, a: _list_rows(hybrid._WORD, a, conn, WORDS["fin"])),
    ("_FUZZY", lambda conn, a: _list_rows(hybrid._FUZZY, a, conn, "aardvrk")),
    ("search, meaning", lambda conn, a: _search(conn, a, "", axis(a.axes["fin"]))),
    ("_MEANING", lambda conn, a: _meaning_rows(hybrid._MEANING, a)(conn)),
    ("_MEANING_EXACT", lambda conn, a: _meaning_rows(hybrid._MEANING_EXACT, a)(conn)),
    (
        "_DETAILS",
        lambda conn, a: hybrid._hits(
            conn, a.org.c, [FusedPiece(p, 1.0, None, None, None) for p in a.pieces["fin"]]
        ),
    ),
    (
        "_NEIGHBOURS",
        lambda conn, a: hybrid.neighbours(conn, customer_id=a.org.c, hits=[_hit(a, "fin")], size=1),
    ),
    (
        "_TOPIC_PIECES",
        lambda conn, a: hybrid.topic_pieces(
            conn, customer_id=a.org.c, terms=[f"topic {WORDS['fin']}"], hits=[_hit(a, "fin")], per_document=5
        ),
    ),
    (
        "_SECTION_PIECES",
        lambda conn, a: hybrid.section_pieces(
            conn,
            customer_id=a.org.c,
            keywords=[WORDS["fin"]],
            embedding=None,
            recipe=RECIPE,
            hits=[],
            min_score=0.6,
            per_section=5,
        ),
    ),
    (
        "_CARDS",
        lambda conn, a: hybrid.cards(conn, customer_id=a.org.c, document_ids=[a.id("fin"), a.id("sales")]),
    ),
    (
        "_STORIES",
        lambda conn, a: [
            s.document_id
            for s in read_stories(
                conn, customer_id=a.org.c, user_id=a.user("plain"), recipe=None, embedding=None
            )
        ],
    ),
    (
        "_WHOLE",
        lambda conn, a: hybrid.whole(conn, customer_id=a.org.c, scope=EVERYTHING, max_bytes=10**6) or [],
    ),
]


@pytest.mark.parametrize(("name", "call"), _CALLS, ids=[name for name, _ in _CALLS])
def test_every_search_statement_reads_nothing_hidden(
    db: Database, ask: AskOrg, name: str, call: _Call
) -> None:
    for person in ("plain", "member"):
        with _as(db, ask, person) as conn:
            found = _documents(call(conn, ask))
        hidden = {ask.id(f) for f in HIDDEN[person]}
        assert not found & hidden, (name, person)
        if person == "member" and name != "_STORIES":  # the stories are the plain reviewer's own
            assert ask.id("fin") in found, name


def test_a_hidden_section_never_takes_one_of_the_two_places(db: Database, ask: AskOrg) -> None:
    """fin's and sal's sections match "ledger" best: read from the table, they would fill s's two
    places and be dropped after, leaving the plain reviewer nothing."""
    with _as(db, ask, "plain") as conn:
        found = hybrid.section_pieces(
            conn,
            customer_id=ask.org.c,
            keywords=["ledger"],
            embedding=None,
            recipe=RECIPE,
            hits=[],
            min_score=0.6,
            per_section=5,
        )
    assert _documents(found) and not _documents(found) & {ask.id(f) for f in HIDDEN["plain"]}


def test_whole_scope_mode_is_chosen_from_what_the_person_sees(db: Database, ask: AskOrg) -> None:
    """_WHOLE_SIZE: the plain reviewer's pieces fit in exactly their own size; with the hidden
    files' pieces counted they would not, and whole-scope mode would be skipped."""
    with _as(db, ask, "plain") as conn:
        size = conn.execute(
            text(
                "SELECT sum(octet_length(p.text) + octet_length(p.context_header)) FROM pieces p"
                " JOIN visible_documents d ON d.id = p.document_id WHERE p.customer_id = :c"
            ),
            {"c": ask.org.c},
        ).scalar_one()
        found = hybrid.whole(conn, customer_id=ask.org.c, scope=EVERYTHING, max_bytes=int(size))
    assert found is not None and _documents(found) == {ask.id("sales"), ask.id("given")}


def test_a_hidden_word_weighs_no_word_and_counts_no_candidate(db: Database, ask: AskOrg) -> None:
    """ "ledger" is on every file's pieces: the plain reviewer's list holds exactly the visible ones."""
    with _as(db, ask, "plain") as conn:
        found = _search(conn, ask, "ledger")
    assert _documents(found) == {ask.id("sales"), ask.id("given")}
    assert len(found) == 4


@contextmanager
def _statements(db: Database) -> Iterator[list[str]]:
    ran: list[str] = []

    def record(_c: Any, _cursor: Any, statement: str, *_: Any) -> None:
        ran.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        yield ran
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


def test_a_person_who_sees_few_vectors_gets_the_exact_scan_only(db: Database, ask: AskOrg) -> None:
    """PRD 7 section 7: below SEARCH_EXACT_BELOW visible vectors the index is never walked; at 0
    it always is first. Either way the same pieces, and the walk's own settings are the given ones."""
    walk, exact = "ORDER BY distance + 0, id", "ORDER BY distance, id LIMIT"
    vector = axis(ask.axes["sales"])
    with _as(db, ask, "plain") as conn, _statements(db) as ran:
        small = _search(conn, ask, "", vector, max_scan_tuples=1234)
        tuples = conn.execute(text("SELECT current_setting('hnsw.max_scan_tuples')")).scalar_one()
    assert not any(walk in s for s in ran) and any(exact in s for s in ran)
    assert tuples == "1234"
    with _as(db, ask, "plain") as conn, _statements(db) as ran:
        walked = _search(conn, ask, "", vector, exact_below=0)
    assert any(walk in s for s in ran)
    assert [h.piece_id for h in walked] == [h.piece_id for h in small]
    assert small[0].piece_id == ask.pieces["sales"][0]


def test_a_meaning_list_cut_by_the_timeout_is_logged(
    db: Database, ask: AskOrg, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 200)
    monkeypatch.setattr(
        hybrid,
        "_VISIBLE_VECTORS",
        text(
            "SELECT count(*) FROM pg_sleep(5) WHERE :c <> '' AND :recipe <> '' AND :most >= 0 AND :vec <> ''"
        ),
    )
    with _as(db, ask, "plain") as conn:
        found = _search(conn, ask, "dingo", axis(ask.axes["sales"]))
    assert [h.piece_id for h in found] == [ask.pieces["sales"][0]] and found[0].meaning_score is None
    assert [r.code for r in caplog.records if hasattr(r, "code")] == ["meaning_timeout"]


def test_no_person_sees_nothing(db: Database, ask: AskOrg) -> None:
    """Fail closed: the API role with no person named finds nothing, by any list."""
    with _app(db).customer(ask.org.c) as conn:
        assert _search(conn, ask, "ledger", axis(0)) == []
        assert hybrid.cards(conn, customer_id=ask.org.c, document_ids=[ask.id("sales")]) == {}


def test_a_random_id_and_a_hidden_one_answer_alike(db: Database, ask: AskOrg) -> None:
    """A scope naming a hidden file finds what a scope naming a file that never was finds."""
    from kaagaz.search.scope import Scope

    with _as(db, ask, "plain") as conn:
        replies = [
            hybrid.search(
                conn,
                customer_id=ask.org.c,
                query_text="ledger aardvark",
                embedding=axis(0),
                limit=10,
                recipe=RECIPE,
                ef_search=EF_SEARCH,
                scope=Scope(document_ids=(d,)),
            )
            for d in (ask.id("fin"), str(uuid.uuid4()))
        ]
    assert replies == [[], []]


# --- the answering statements (commit 2) --------------------------------------------------


def _value_id(ask: AskOrg, name: str, field: str = "total_amount") -> str:
    rows = _owner(
        ask.db,
        "SELECT id FROM extracted_values WHERE document_id = :d AND field = :f AND record_key = 'document'",
        {"d": ask.id(name), "f": field},
    )
    return str(rows[0][0])


def _ports(ask: AskOrg) -> dict[str, Any]:
    app = _app(ask.db)
    return {
        "exhaustive": PgExhaustive(app),
        "fixed": PgFixedQueries(app),
        "owners": PgOwners(app),
        "unchecked": PgUncheckedValues(app),
        "reading": PgReadingQuality(app),
        "vocabulary": PgVocabulary(app),
    }


def _refs(rows: Any) -> set[str]:
    return {ref.document_id for row in rows for ref in [*row.values, *row.unsure, *row.totals]}


def _rows(ask: AskOrg, user: str, statement: Any, params: dict[str, Any]) -> list[Any]:
    with _app(ask.db).user(ask.org.c, user) as conn:
        return list(conn.execute(statement, params))


def _value_refs_as(ask: AskOrg, user: str, ids: list[str]) -> dict[str, Any]:
    with _app(ask.db).user(ask.org.c, user) as conn:
        return fixed_queries._value_refs(conn, ask.org.c, ids, EVERYTHING)


def _pieces(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    params = {"c": a.org.c, "w": WORDS["fin"], "l": WORDS["fin"], "documents": [a.id("fin")]}
    return {str(r[1]) for r in _rows(a, u, exhaustive._PIECES, params)}, None


def _documents_with(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    total, found = p["exhaustive"].documents_with(a.org.c, "ledger", EVERYTHING, 10, user_id=u)
    return {h.document_id for hits in found for h in hits}, total


def _mentions(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    total, rows = p["exhaustive"].mentions(a.org.c, ["ledger"], EVERYTHING, [], 10, user_id=u)
    return {r["document_id"] for r in rows}, total


def _figures_of(invoices: bool) -> "_Answering":
    def call(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
        found = p["fixed"].figures(
            a.org.c, "total_amount", ["invoice_date"], None, None, invoices=invoices, user_id=u
        )
        return {f.value.document_id for f in found if f.value}, len(found)

    return call


def _counted(rows: Any) -> tuple[set[str], int | None]:
    return _refs(rows), int(rows[0].number) if rows else 0


def _candidates(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    asked = [Entity("person", f"Ramesh {WORDS['fin']}")]
    found = p["vocabulary"].candidates(a.org.c, asked, user_id=u)
    return {d for entries in found for entry in entries for d in entry.documents}, None


def _flagged(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    pages, total = p["reading"].flagged_pages(a.org.c, user_id=u)
    return {f.document_id for f in pages}, total


def _every(p: dict[str, Any], a: AskOrg, u: str) -> tuple[set[str], int | None]:
    total, rows = p["owners"].every(a.org.c, list(OWNING), EVERYTHING, 50, user_id=u)
    return {d for d, _ in rows}, total


def _fin_if(found: Any, a: AskOrg) -> set[str]:
    return {a.id("fin")} if found else set()


# Each answering statement: (name, call(ports, ask, user) -> (document ids, a count or None)).
_Answering = Callable[[dict[str, Any], AskOrg, str], tuple[set[str], int | None]]
_FIN_PERSON = latin_key(f"Ramesh {WORDS['fin']}")
_FIN_PLACE = latin_key(f"{WORDS['fin']}pur")
_ANSWERING: list[tuple[str, _Answering]] = [
    ("_DOCUMENTS", _documents_with),  # "N documents mention X": its count too
    ("_MENTIONS", _mentions),  # "Also mentions" and its total
    ("_PIECES", _pieces),  # a forged document
    (  # only fin has a GST figure
        "_AVAILABLE_FIELDS",
        lambda p, a, u: (_fin_if(p["fixed"].available_fields(a.org.c, ["gst_amount"], user_id=u), a), None),
    ),
    ("_FIGURES", _figures_of(False)),
    ("_FIGURES invoiced", _figures_of(True)),
    (
        "_TOP_GROUP",
        lambda p, a, u: (lambda rows: (_refs(rows), sum(int(r.records) for r in rows)))(
            p["fixed"].top_group(a.org.c, "total_amount", ["seller_name"], [], None, None, True, user_id=u)
        ),
    ),
    (
        "_EXTREME_VALUE",
        lambda p, a, u: (
            _refs(p["fixed"].extreme_value(a.org.c, "total_amount", [], None, None, True, user_id=u)),
            None,
        ),
    ),
    (  # "I found 4 documents"
        "_COUNT_DOCUMENTS",
        lambda p, a, u: _counted(
            p["fixed"].count_documents(a.org.c, None, None, date_fields=["invoice_date"], user_id=u)
        ),
    ),
    (  # "I found 4 invoices"
        "_COUNT_INVOICES",
        lambda p, a, u: _counted(p["fixed"].count_invoices(a.org.c, None, None, user_id=u)),
    ),
    (  # forged ids
        "_VALUE_REFS",
        lambda p, a, u: (
            {
                r.document_id
                for r in _value_refs_as(a, u, [_value_id(a, "fin"), _value_id(a, "sales")]).values()
            },
            None,
        ),
    ),
    (
        "_RESOLVE",
        lambda p, a, u: (
            _fin_if(p["owners"].resolve(a.org.c, [_FIN_PERSON], EVERYTHING, user_id=u), a),
            None,
        ),
    ),
    (
        "_OWNED",
        lambda p, a, u: (
            {d for d, _, _ in p["owners"].owned(a.org.c, [_FIN_PERSON], EVERYTHING, user_id=u)},
            None,
        ),
    ),
    (
        "_KEYED",
        lambda p, a, u: (
            {d for d, _ in p["owners"].keyed(a.org.c, "place", [_FIN_PLACE], EVERYTHING, user_id=u)},
            None,
        ),
    ),
    ("_EVERY_RECORD", _every),  # the owners' total
    (  # a forged document
        "_SHARES_AS_PRINTED",
        lambda p, a, u: (
            {
                ref.document_id
                for _, _, ref in p["owners"].shares(a.org.c, [a.id("fin")], EVERYTHING, user_id=u)
            },
            None,
        ),
    ),
    (
        "_CARD_DOCUMENTS",
        lambda p, a, u: (
            {r["id"] for r in p["unchecked"].documents_with(a.org.c, ["total_amount"], user_id=u)},
            None,
        ),
    ),
    ("_FLAGGED_PAGES", _flagged),  # "3 pages could not be read"
    (  # forged documents, both arms
        "_WORD_MARKS",
        lambda p, a, u: (
            {
                m.document_id
                for m in p["reading"].word_marks(a.org.c, [a.id("fin"), a.id("sales")], user_id=u)
            },
            None,
        ),
    ),
    ("_CANDIDATES", _candidates),  # "Did you mean"
    (
        "_NEAR",
        lambda p, a, u: (
            {d for e in p["vocabulary"].near(a.org.c, [latin_key("Satara")], user_id=u) for d in e.documents},
            None,
        ),
    ),
    (
        "_ANY_KEYS",
        lambda p, a, u: (
            _fin_if(p["vocabulary"].within([a.id("fin")]).any_keys(a.org.c, user_id=u), a),
            None,
        ),
    ),
]
# How many of each count the plain reviewer (sales, given) and Finance's member (and fin, held) see.
_COUNTS = {"plain": 2, "member": 4}


@pytest.mark.parametrize(("name", "call"), _ANSWERING, ids=[name for name, _ in _ANSWERING])
def test_every_answering_statement_reads_and_counts_nothing_hidden(
    db: Database, ask: AskOrg, name: str, call: _Answering
) -> None:
    ports = _ports(ask)
    for person in ("plain", "member"):
        found, count = call(ports, ask, ask.user(person))
        assert not found & {ask.id(f) for f in HIDDEN[person]}, (name, person, found)
        if count is not None:
            assert count == _COUNTS[person], (name, person, count)
        if person == "member":
            assert ask.id("fin") in found, name


def test_without_a_person_every_answering_port_finds_nothing(db: Database, ask: AskOrg) -> None:
    """Fail closed: ``user_id=None`` in the API role reads through the view as nobody."""
    ports = _ports(ask)
    assert ports["fixed"].count_invoices(ask.org.c, None, None, user_id=None) == []
    assert ports["exhaustive"].mentions(ask.org.c, ["ledger"], EVERYTHING, [], 10, user_id=None) == (0, [])
    assert ports["vocabulary"].any_keys(ask.org.c, user_id=None) is False


def test_a_hidden_sheet_changes_no_visible_figure(db: Database, ask: AskOrg) -> None:
    """Question 12: the labelled and sums steps read every document's sheets, matched per document.
    A total row added to fin's sheet leaves the plain reviewer's figures as they were."""
    fixed, plain = _ports(ask)["fixed"], ask.user("plain")

    def figures() -> list[Any]:
        found: list[Any] = fixed.figures(
            ask.org.c, "total_amount", [], None, None, invoices=False, user_id=plain
        )
        return found

    before = figures()
    for row, said in ((2, "500"), (3, "500"), (4, "1000")):
        params = {"c": ask.org.c, "d": ask.id("fin"), "key": f"sheet:1:row:{row}", "f": "total_amount"}
        _owner(db, _VALUE, params | {"t": said, "n": int(said), "day": None, "label": "verified"})
    _owner(
        db,
        "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"
        " VALUES (:c, :d, 5, 1, 1, 'A: Total | C: 1,000',"
        ' \'[{"kind": "cell", "page": 1, "row": 4, "column": 1}]\', \'verified\')',
        {"c": ask.org.c, "d": ask.id("fin")},
    )
    assert figures() == before


def test_a_did_you_mean_pick_records_its_document_and_aliases_only_for_who_sees_it(
    db: Database, ask: AskOrg
) -> None:
    """Question 9: a pick of one document records it and the page its name is on, through the
    view; an approved word tied to a hidden document makes no alias of a visible one's name."""
    name = f"Ramesh {WORDS['fin']}"

    def pick(document: str) -> Pending:
        return Pending("Land of Rameshji?", "person", "Rameshji", (), (), 1, (name,), True, None, (document,))

    for person, document, page in (("plain", "fin", None), ("member", "fin", 1), ("plain", "sales", None)):
        with _as(db, ask, person) as conn:
            assert picked_page(conn, ask.org.c, ask.id(document), pick(ask.id(document))) == page, person
    with db.customer(ask.org.c) as conn:
        insert_key(conn, ask.org.c, ask.id("sales"), "person", name)  # the same name on a visible file
    with _as(db, ask, "plain") as conn:
        assert picked_page(conn, ask.org.c, ask.id("sales"), pick(ask.id("sales"))) == 1
    _owner(
        db,
        "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold, meaning,"
        " meaning_fold, document_id, page_no, status, decided_by, decided_at)"
        " VALUES (:c, :u, 'Rameshji', 'rameshji', :m, :m, :d, 1, 'approved', :u, now())",
        {"c": ask.org.c, "u": ask.user("admin"), "m": latin_key(name), "d": ask.id("fin")},
    )
    vocabulary = _ports(ask)["vocabulary"]
    for person, sees in (("plain", False), ("member", True)):
        found = vocabulary.candidates(ask.org.c, [Entity("person", "Rameshji")], user_id=ask.user(person))
        assert any(entry.aliased for entry in found[0]) == sees, person


# --- the chat statements, the words and Home (commit 3) --------------------------------------


def _answered(ask: AskOrg, user: str, cited: list[str], *, status: str = "answered") -> str:
    """An answered message in a new conversation of ``user``, citing page 1 of ``cited``."""
    answer = {
        "status": status,
        "normalized_question": "total",
        "citations": [{"n": n, "document_id": d, "page_no": 1} for n, d in enumerate(cited, start=1)],
        "sentences": [{"text": "The total is 7001.", "citations": list(range(1, len(cited) + 1))}],
    }
    rows = _owner(
        ask.db,
        "WITH c AS (INSERT INTO conversations (customer_id, user_id, title)"
        " VALUES (:c, :u, 'Q') RETURNING id),"
        " q AS (INSERT INTO messages (customer_id, conversation_id, role, text)"
        " SELECT :c, id, 'user', 'What is the total?' FROM c)"
        " INSERT INTO messages (customer_id, conversation_id, role, text, answer)"
        " SELECT :c, id, 'assistant', 'The total is 7001.', CAST(:a AS jsonb) FROM c RETURNING id",
        {"c": ask.org.c, "u": user, "a": json.dumps(answer)},
    )
    return str(rows[0][0])


def _pick(ask: AskOrg, message: str, user: str, document: str) -> None:
    _owner(
        ask.db,
        "INSERT INTO source_pins (customer_id, message_id, user_id, document_id, page_no, previous_rank)"
        " VALUES (:c, :m, :u, :d, 1, 2)",
        {"c": ask.org.c, "m": message, "u": user, "d": document},
    )


def _approved_word(ask: AskOrg, document: str | None, word: str) -> None:
    _owner(
        ask.db,
        "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold, meaning,"
        " meaning_fold, document_id, page_no, status, decided_by, decided_at)"
        " VALUES (:c, :u, :w, :w, 'meaning', 'meaning', :d,"
        " CASE WHEN CAST(:d AS uuid) IS NULL THEN NULL ELSE 1 END, 'approved', :u, now())",
        {"c": ask.org.c, "u": ask.user("admin"), "w": word, "d": document},
    )


def test_the_chat_statements_name_no_hidden_document(db: Database, ask: AskOrg) -> None:
    """document_states, live_document_names, the newest pick, Most relevant picks and other
    people's Useful marks, each as the plain reviewer and as Finance's member."""
    for person in ("plain", "member"):
        me = ask.user(person)
        message = _answered(ask, me, [ask.id("fin"), ask.id("sales")])
        _pick(ask, message, me, ask.id("sales"))
        _pick(ask, message, me, ask.id("fin"))  # the newest pick
        other = _answered(ask, ask.user("admin"), [ask.id("fin"), ask.id("sales")])
        _owner(
            db,
            "INSERT INTO feedback (customer_id, message_id, user_id, rating) VALUES (:c, :m, :u, 'up')",
            {"c": ask.org.c, "m": other, "u": ask.user("admin")},
        )
        sees_fin = person == "member"
        ids = [ask.id("fin"), ask.id("sales")]
        with _as(db, ask, person) as conn:
            states = chat_sql.document_states(conn, ask.org.c, ids)
            names = chat_sql.live_document_names(conn, ask.org.c, ids)
            conversation = _owner(db, "SELECT conversation_id FROM messages WHERE id = :m", {"m": message})[
                0
            ][0]
            rows = chat_sql.messages_with_feedback(conn, ask.org.c, str(conversation), me)
            pins = {row["document_id"] for row in chat_sql.pins_for(conn, ask.org.c, me, None)}
            learned = {row["document_id"] for row in chat_sql.learned_for(conn, ask.org.c, me)}
        assert set(states) == ({*ids} if sees_fin else {ask.id("sales")}), person
        assert set(names) == set(states), person
        picked = [str(row["pin_document_id"]) for row in rows if row["role"] == "assistant"]
        assert picked == [ask.id("fin") if sees_fin else ask.id("sales")], person
        # Most relevant reads each answer's newest pick only: fin's, so none for the plain reviewer.
        # Changed on purpose (#606): under the database rule fin's pick is not in source_pins for
        # them, so their own older pick of sales is the newest they have (FEWER_OK; nothing hidden).
        assert pins == ({ask.id("fin")} if sees_fin else set()) or (
            not sees_fin and pins == {ask.id("sales")}
        ), person
        assert learned == set(states), person


def test_a_word_made_on_a_hidden_page_is_never_used_or_echoed(db: Database, ask: AskOrg) -> None:
    """Decision 9: a word made on a page (or a Did you mean pick of one document) helps in any scope
    for whoever sees its document, and never for one who doesn't; the repeat key's words leave it
    out too, so it moves no plain reviewer's key. A same word tied to a document is a duplicate of
    the organisation's own."""
    _approved_word(ask, None, "everyone")
    with _as(db, ask, "plain") as conn:
        before = conn.execute(ready._WORDS, {"c": ask.org.c, "u": ask.user("plain")}).scalar_one()
    _approved_word(ask, ask.id("fin"), "secretword")
    for person, sees in (("plain", False), ("member", True)):
        with _as(db, ask, person) as conn:
            words = {w.word for w in organization_words.approved(conn, ask.org.c)}
            after = conn.execute(ready._WORDS, {"c": ask.org.c, "u": ask.user(person)}).scalar_one()
        assert words == ({"everyone", "secretword"} if sees else {"everyone"}), person
        assert (after == before) != sees, person
    texts = {"word": "everyone", "word_fold": "everyone", "meaning": "meaning", "meaning_fold": "meaning"}
    with _as(db, ask, "member") as conn:
        tied = organization_words.submit(
            conn, ask.org.c, ask.user("member"), **texts, document_id=ask.id("sales"), page_no=1
        )
    assert tied is None  # the organisation's own word already says it


def test_home_offers_only_questions_about_what_the_person_sees(db: Database, ask: AskOrg) -> None:
    """Only Finance holds land records: the plain reviewer is not offered the land question; a tag
    scope's documents are those they see; the stamp moves when who sees what does."""
    _owner(db, "UPDATE documents SET doc_type = 'land_record' WHERE id = :d", {"d": ask.id("fin")})
    _owner(db, "UPDATE documents SET doc_type = 'invoice' WHERE id = :d", {"d": ask.id("sales")})
    land = ready.TYPE_QUESTIONS["land_record"]
    with db.customer(ask.org.c) as conn:
        tag = insert_tag(conn, ask.org.c, ask.user("admin"), "Ledgers")
        for name in ("fin", "sales"):
            tag_document(conn, ask.org.c, ask.id(name), tag)
    for person, sees in (("plain", False), ("member", True)):
        with _as(db, ask, person) as conn:
            offered = [q for q, _ in ready.questions(conn, ask.org.c, ask.user(person))]
            tagged = ready.tagged(conn, ask.org.c, [str(tag)])
        assert (land in offered) == sees and ready.TYPE_QUESTIONS["invoice"] in offered, person
        assert (len(tagged) == 2) == sees and any(ask.id("sales") in t for t in tagged), person
    with _as(db, ask, "plain") as conn:
        before = ready.stamp(conn, ask.org.c)
    _owner(
        db,
        "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)",
        {"c": ask.org.c, "d": ask.id("fin"), "u": ask.user("plain"), "by": ask.user("admin")},
    )
    with _as(db, ask, "plain") as conn:
        assert ready.stamp(conn, ask.org.c) != before


# --- old answers, memory and kept answers (commit 4) ------------------------------------------


def _share(ask: AskOrg, name: str, *, give: bool) -> None:
    """Give ``name`` to the plain reviewer directly (R2), or take it back."""
    params = {"c": ask.org.c, "d": ask.id(name), "u": ask.user("plain"), "by": ask.user("admin")}
    if give:
        _owner(
            ask.db,
            "INSERT INTO document_shares (customer_id, document_id, user_id, given_by)"
            " VALUES (:c, :d, :u, :by)",
            params,
        )
    else:
        _owner(ask.db, "DELETE FROM document_shares WHERE document_id = :d AND user_id = :u", params)


def _post(ask: AskOrg, person: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    reply = ask.org.people[person].client.post("/api/chat", json=body, headers=CSRF)
    assert reply.status_code == status, reply.text
    found: dict[str, Any] = reply.json()
    return found


SECRET = f"The {WORDS['fin']} total is {AMOUNTS['fin']}."


def _fin_answer(ask: AskOrg) -> dict[str, Any]:
    """An answer citing fin and sales, its first sentence fin's own words."""
    answer = _cited_answer(ask.id("fin"), ask.id("sales"))
    answer["sentences"][0]["text"] = SECRET
    return answer


def test_an_answer_behind_a_file_taken_back_is_replaced_whole_everywhere(db: Database, ask: AskOrg) -> None:
    """Fin given to the plain reviewer, asked about, then taken back: the conversation, the retried
    reply and the Questions panel show only the line; a follow-up gets no fin sentence, no fin id
    and no memory; the memory is made again from the shown answers. Given back, it all returns."""
    plain = ask.org.people["plain"]
    _share(ask, "fin", give=True)
    ask.api.answers.reply = _fin_answer(ask)
    asked = str(uuid.uuid4())
    first = _post(ask, "plain", {"question": "What is the total?", "question_id": asked})
    conversation = first["conversation_id"]
    _owner(
        db,
        "UPDATE conversations SET summary = 'About the total', memory = CAST(:m AS jsonb) WHERE id = :id",
        {
            "id": conversation,
            "m": json.dumps({"v": VERSION, "documents": [ask.id("fin")], "names": [WORDS["fin"]]}),
        },
    )
    _share(ask, "fin", give=False)

    shown = plain.client.get(f"/api/conversations/{conversation}").json()["messages"][1]
    assert shown["answer"]["status"] == NO_ACCESS and shown["text"] == NO_ACCESS_LINE
    retried = _post(ask, "plain", {"question": "What is the total?", "question_id": asked})["answer"]
    panel = plain.client.get(f"/api/documents/{ask.id('sales')}/questions").json()["items"]
    assert retried["answer"]["status"] == NO_ACCESS and retried["text"] == NO_ACCESS_LINE
    assert [item["answer"]["status"] for item in panel] == [NO_ACCESS]
    for seen in (shown, retried, panel):
        assert str(AMOUNTS["fin"]) not in json.dumps(seen) and ask.id("fin") not in json.dumps(seen)

    ask.api.answers.remembered, ask.api.answers.reply = None, answer_json()
    _post(ask, "plain", {"question": "And the tax?", "conversation_id": conversation})
    assert ask.api.answers.histories[-1] == [Turn("What is the total?", None)]
    assert ask.api.answers.cited[-1] == [] and ask.api.answers.memories[-1] == NO_MEMORY
    turns, previous, cited = ask.api.answers.summarised[-1]  # made again at once (question 7)
    assert previous == NO_MEMORY and ask.id("fin") not in cited and SECRET not in repr(turns)

    _share(ask, "fin", give=True)
    again = plain.client.get(f"/api/conversations/{conversation}").json()["messages"][1]
    assert again["answer"]["status"] == "answered" and again["text"] != NO_ACCESS_LINE


def test_a_change_of_access_moves_the_repeat_key(db: Database, ask: AskOrg) -> None:
    """The same question again is served from its stored answer; once a file is given (or taken
    back), the access version moves the key, and it is answered fresh."""
    ask.api.answers.reply = {**_cited_answer(ask.id("sales")), "model_used": True}
    question = {"question": "What is the sales total?"}
    _post(ask, "plain", question)
    asked = len(ask.api.answers.asked)
    _post(ask, "plain", question)
    assert len(ask.api.answers.asked) == asked  # a repeat: no new answer
    _share(ask, "fin", give=True)
    _post(ask, "plain", question)
    assert len(ask.api.answers.asked) == asked + 1
    _share(ask, "fin", give=False)
    _post(ask, "plain", question)
    assert len(ask.api.answers.asked) == asked + 2


def test_a_reply_to_a_clarify_whose_choices_are_hidden_is_gone(db: Database, ask: AskOrg) -> None:
    """Question 14: 409 CLARIFY_GONE, as for a closed one; nothing is answered or suggested."""
    _share(ask, "fin", give=True)
    ask.api.answers.reply = _clarify(ask.id("fin"))
    asked = _post(ask, "plain", {"question": "Land in Puny?"})
    _share(ask, "fin", give=False)
    body = {
        "conversation_id": asked["conversation_id"],
        "reply": {"message_id": asked["answer"]["id"], "choice_ids": ["c1"]},
    }
    gone = _post(ask, "plain", body, 409)
    assert gone["error"]["message"] == CLARIFY_GONE and ask.api.answers.resumed == []


def test_answer_quality_shows_source_not_visible_with_no_question_or_reason(
    db: Database, ask: AskOrg
) -> None:
    """Question 4: the person named on Salaries rated an answer citing sal Not useful; an Admin
    not named on Salaries sees the item, but not its question, reason or answer."""
    ask.api.answers.reply = _cited_answer(ask.id("sal"))
    named = ask.org.people["named"]
    asked = named.client.post("/api/chat", json={"question": "Ravi's salary?"}, headers=CSRF).json()
    rated = named.client.post(
        f"/api/messages/{asked['answer']['id']}/feedback",
        json={
            "rating": "down",
            "reason": "Ravi is wrong",
            "reason_codes": ["wrong_figure"],
            "notice_shown": True,
        },
        headers=CSRF,
    )
    assert rated.status_code == 204
    items = ask.org.people["admin"].client.get("/api/admin/answer-quality").json()["items"]
    (item,) = [i for i in items if i["message_id"] == asked["answer"]["id"]]
    assert (item["question"], item["reason"], item["reason_codes"]) == (None, None, [])
    assert item["answer"]["status"] == NO_ACCESS and "Ravi" not in json.dumps(item)


def test_a_hidden_scope_id_answers_as_a_random_one(db: Database, ask: AskOrg) -> None:
    """Hard rule 3: search inside fin answers exactly as inside a file that never was, understood
    words included (a word made on fin's page). Ask's own: test_red_team's scoped question."""
    _approved_word(ask, ask.id("fin"), "secretword")
    plain = ask.org.people["plain"].client
    replies = [
        plain.get("/api/search", params={"q": "secretword ledger", "document_ids": [d]}).json()
        for d in (ask.id("fin"), str(uuid.uuid4()))
    ]
    assert replies[0] == replies[1]


def test_homes_list_is_made_again_when_access_changes(db: Database, ask: AskOrg) -> None:
    """ready.SHOWN keeps Home's list with the access version it was made at: fin given, its land
    question is offered; taken back, the list is made again without it."""
    _owner(db, "UPDATE documents SET doc_type = 'land_record' WHERE id = :d", {"d": ask.id("fin")})
    land, plain = ready.TYPE_QUESTIONS["land_record"], ask.org.people["plain"].client

    def offered() -> list[str]:
        return [item["text"] for item in plain.get("/api/chat/ready-questions").json()["items"]]

    _share(ask, "fin", give=True)
    assert land in offered()
    _share(ask, "fin", give=False)
    assert land not in offered()


# --- red team and differential (commit 6) ------------------------------------------------------


class _Recording(Model):
    """test_ask_speed_db's model (every text on axis 0, fin's own; the draft quotes its first
    evidence block), keeping every prompt and every embedded text; ``down``: every chat call fails."""

    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.prompts: list[str] = []
        self.embedded: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return super().embed(texts)

    def chat_json(self, *, system: str, user: str, schema: Any, **kwargs: Any) -> dict[str, Any]:
        self.prompts.append(f"{system}\n{user}")
        if self.down:
            raise ModelUnavailable("down")
        if system != rewrite.SYSTEM_PROMPT and not evidence_blocks(user):
            return {"status": "not_found", "sentences": []}
        return super().chat_json(system=system, user=user, schema=schema, **kwargs)


RED_TEAM = (
    "What is the total amount?",
    "ledger",
    "Who owns the land in Satara?",
    "How many invoices are there?",
    "Which seller has the highest total?",
    "Which pages could not be read?",
    "Tell me about aardvark",
)


def _hidden_marks(ask: AskOrg) -> list[str]:
    """What of the plain reviewer's hidden files must never reach a prompt, an embedded text or a reply."""
    marks = []
    for name in HIDDEN["plain"]:
        word = WORDS[name]
        marks += [ask.id(name), *ask.pieces[name], f"{name}.pdf", str(AMOUNTS[name]), f"Invoice {word}"]
        marks += [f"Seller {word}", f"Ramesh {word}".casefold(), f"{word}pur", f"topic {word}"]
    return marks


def _answers(ask: AskOrg, model: Any, scope: Any = EVERYTHING, choice: Any = None) -> list[str]:
    settings = make_settings(model_embed="test-embed")
    service = AnswerService(db=_app(ask.db), model=model, settings=settings, clock=lambda: NOW)
    return [
        json.dumps(
            service.answer(ask.org.c, q, scope=scope, user_id=ask.user("plain"), choice=choice).to_json(),
            sort_keys=True,
        )
        for q in RED_TEAM
    ]


_LEAD = Agent("google/gemini-3.8-flash", "google-vertex", "Lead")
_OTHER = Agent("anthropic/claude-sonnet-5.5", "google-vertex", "Other")
_CHOICES = {"precision": ChatChoice([_LEAD], "high"), "ensemble": ChatChoice([_LEAD, _OTHER], "medium")}


@pytest.mark.parametrize("mode", ["model", "model down", "no model", "precision", "ensemble", "streamed"])
def test_red_team_nothing_hidden_reaches_a_prompt_or_a_reply(db: Database, ask: AskOrg, mode: str) -> None:
    """PRD 7 part 3: the real AnswerService, as the plain reviewer, every route these questions
    take (search, whole scope, totals, counts, owners, pages not read, Did you mean), with the
    model, down, off, Precision, Ensemble and streamed (every event it sends)."""
    model = None if mode == "no model" else _Recording(down=mode == "model down")
    events: list[str] = []
    token = stream.EVENTS.set(stream.Events(lambda name, data: events.append(json.dumps(data))))
    try:
        replies = _answers(ask, model, choice=_CHOICES.get(mode))
    finally:
        stream.EVENTS.reset(token)
    texts = replies + events + ([] if model is None else model.prompts + model.embedded)
    seen = "\n".join(texts).casefold()
    # Whole words only: an amount such as 7003 may sit inside a visible piece's random id.
    marks = [re.escape(m.casefold()) for m in _hidden_marks(ask)]
    assert [m for m in marks if re.search(f"(?<![0-9a-z]){m}(?![0-9a-z])", seen)] == []
    assert any(ask.id("sales") in reply for reply in replies)  # the visible file does answer


def test_a_translated_search_finds_nothing_hidden(db: Database, ask: AskOrg) -> None:
    """The fused search (CX6's aspects, the translation): a translation naming fin's word, on fin's
    own vector, finds nothing of fin for the plain reviewer; Finance's member finds it."""
    retriever = PgRetriever(_app(db), recipe=RECIPE, ef_search=EF_SEARCH, neighbours=1)
    fin = [(WORDS["fin"], axis(ask.axes["fin"]))]
    for person, sees in (("plain", False), ("member", True)):
        found = retriever.search_translated(
            ask.org.c, "ledger", None, fin, 50, EVERYTHING, user_id=ask.user(person)
        )
        assert (ask.id("fin") in {hit.document_id for hit in found.hits}) == sees, person


def test_a_scope_of_a_hidden_file_answers_as_a_random_one(db: Database, ask: AskOrg) -> None:
    from kaagaz.search.scope import Scope

    _approved_word(ask, ask.id("fin"), "secretword")
    replies = [
        _answers(ask, _Recording(), Scope(document_ids=(d,))) for d in (ask.id("fin"), str(uuid.uuid4()))
    ]
    assert replies[0] == replies[1]


def test_differential_the_hidden_file_deleted_forever_changes_no_reply(db: Database, ask: AskOrg) -> None:
    """The plain reviewer's replies with fin there and hidden equal their replies once it is gone."""
    before = _answers(ask, _Recording())
    _owner(db, "DELETE FROM documents WHERE id = :d", {"d": ask.id("fin")})
    assert _answers(ask, _Recording()) == before


# --- review of #602 ------------------------------------------------------------------------------


def test_no_hidden_piece_is_in_a_list_before_the_details(db: Database, ask: AskOrg) -> None:
    """The pre-filter, at the lists themselves (_lists: words, typos, the walk and its exact
    fallback): no hidden piece is a candidate, before search() fetches any detail."""
    for person in ("plain", "member"):
        for query, vector in ((WORDS["fin"], None), ("aardvrk", None), ("", axis(ask.axes["fin"]))):
            owners: dict[str, str] = {}
            with _as(db, ask, person) as conn:
                hybrid._lists(
                    conn, ask.org.c, query, vector, RECIPE, EF_SEARCH, EVERYTHING, NO_EXPANSION, owners,
                    hybrid.MAX_SCAN_TUPLES, 0,
                )  # fmt: skip
            found = set(owners.values())
            assert not found & {ask.id(f) for f in HIDDEN[person]}, (person, query)
            assert (ask.id("fin") in found) == (person == "member"), (person, query)


def test_a_walk_cut_by_the_timeout_falls_back_to_the_exact_scan(
    db: Database, ask: AskOrg, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 200)
    monkeypatch.setattr(
        hybrid,
        "_MEANING",
        text(
            "SELECT p.id, p.document_id, 0.0 FROM pieces p CROSS JOIN pg_sleep(5)"
            " WHERE p.customer_id = :c AND :recipe <> '' AND :n > 0 AND length(:vec) > 0"
        ),
    )
    with _as(db, ask, "plain") as conn:
        found = _search(conn, ask, "", axis(ask.axes["sales"]), exact_below=0)
    assert found[0].piece_id == ask.pieces["sales"][0] and found[0].meaning_score is not None
    assert [r.code for r in caplog.records if hasattr(r, "code")] == ["meaning_timeout"]


def test_the_memory_tracks_the_documents_of_a_clarify_it_read(db: Database) -> None:
    """A clarify about fin falls in the summary window: the memory tracks fin; fin hidden, the next
    follow-up gets no memory and no turn naming fin's choice."""
    ask = _ask_org(Api(db, settings=make_settings(chat_summary_every=2)))
    made: list[list[str]] = []

    def remember(turns: Any, memory: Any, cited: Any) -> Memory:
        made.append(list(cited))
        return Memory(summary="About Pune", documents=tuple(dict.fromkeys(cited)), names=("Pune",))

    ask.api.answers.remember = remember  # type: ignore[method-assign]
    _share(ask, "fin", give=True)
    ask.api.answers.reply = _clarify(ask.id("fin"))
    conversation = _post(ask, "plain", {"question": "Land in Puny?"})["conversation_id"]
    ask.api.answers.reply = answer_json()
    _post(ask, "plain", {"question": "What is the total amount?", "conversation_id": conversation})
    assert made and ask.id("fin") in made[-1]
    _share(ask, "fin", give=False)
    _post(ask, "plain", {"question": "And the tax?", "conversation_id": conversation})
    assert ask.api.answers.memories[-1] == NO_MEMORY
    assert "Pune" not in repr(ask.api.answers.histories[-1])


def test_a_memory_from_before_every_document_was_tracked_is_not_trusted(db: Database, ask: AskOrg) -> None:
    """An old memory (no "v": 2) naming something of fin with fin's id missing: not used, and made
    again; one written since is used (the control)."""
    ask.api.answers.reply = answer_json()
    for version, used in ((None, False), (VERSION, True)):
        conversation = _post(ask, "plain", {"question": "What is the total?"})["conversation_id"]
        memory = {"documents": [], "names": [WORDS["fin"]]} | ({} if version is None else {"v": version})
        _owner(
            db,
            "UPDATE conversations SET summary = 'About it', memory = CAST(:m AS jsonb) WHERE id = :id",
            {"id": conversation, "m": json.dumps(memory)},
        )
        summarised = len(ask.api.answers.summarised)
        _post(ask, "plain", {"question": "And the tax?", "conversation_id": conversation})
        assert (ask.api.answers.memories[-1] != NO_MEMORY) == used, version
        assert (len(ask.api.answers.summarised) > summarised) != used, version  # made again


def test_answers_after_a_replaced_one_show_no_reading_and_a_hidden_pick_is_not_named(
    db: Database, ask: AskOrg
) -> None:
    """A clarify on fin, picked, then answered from sales: fin taken back, the clarify is replaced,
    the pick shows as "A choice you made earlier", and the answer after it shows no question as
    read, assumption or understood word (a follow-up may have read the hidden answer)."""
    _share(ask, "fin", give=True)
    ask.api.answers.reply = _clarify(ask.id("fin"))
    first = _post(ask, "plain", {"question": "Land in Puny?"})
    reading = {
        "rewritten_question": "Pune land total",
        "normalized_question": "pune land total",
        "assumptions": ["Pune is the place"],
        "understood_words": [{"word": "Puny", "meaning": "Pune"}],
    }
    ask.api.answers.reply = {**_cited_answer(ask.id("sales")), **reading}
    reply = {"message_id": first["answer"]["id"], "choice_ids": ["c1"]}
    _post(ask, "plain", {"conversation_id": first["conversation_id"], "reply": reply})
    _share(ask, "fin", give=False)
    plain = ask.org.people["plain"].client
    messages = plain.get(f"/api/conversations/{first['conversation_id']}").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1]["answer"]["status"] == NO_ACCESS and messages[2]["text"] == EARLIER_CHOICE
    after = messages[3]["answer"]
    assert [after[key] for key in reading] == [None, "", [], []]
    assert "Pune" not in json.dumps(messages)


def test_a_not_found_answer_inside_a_hidden_file_is_replaced(db: Database, ask: AskOrg) -> None:
    """A not-found answer names no document; its question's scope does: fin taken back, the words
    it understood (a word made on fin's page) are not shown again."""
    _share(ask, "fin", give=True)
    ask.api.answers.reply = {
        **answer_json(status="not_found"),
        "understood_words": [{"word": "secretword", "meaning": "m", "origin": "organization"}],
    }
    asked = _post(ask, "plain", {"question": "secretword?", "scope": {"document_ids": [ask.id("fin")]}})
    _share(ask, "fin", give=False)
    plain = ask.org.people["plain"].client
    shown = plain.get(f"/api/conversations/{asked['conversation_id']}").json()["messages"][1]
    assert shown["answer"]["status"] == NO_ACCESS and "secretword" not in json.dumps(shown)


def test_a_kept_answer_naming_a_hidden_file_is_not_served_under_the_same_key(
    db: Database, ask: AskOrg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source check itself, the access version aside (the stamp held still): a repeat, and a
    ready answer Home made, that name fin are not served once fin is taken back."""
    monkeypatch.setattr(ready, "stamp", lambda conn, customer_id: "held still")
    _share(ask, "fin", give=True)
    ask.api.answers.reply = {**_cited_answer(ask.id("fin")), "model_used": True}
    question = {"question": "What is the fin total?"}
    _post(ask, "plain", question)
    asked = len(ask.api.answers.asked)
    _post(ask, "plain", question)
    assert len(ask.api.answers.asked) == asked  # served as a repeat
    _share(ask, "fin", give=False)
    _post(ask, "plain", question)
    assert len(ask.api.answers.asked) == asked + 1

    _owner(db, "UPDATE documents SET doc_type = 'land_record' WHERE id = :d", {"d": ask.id("fin")})
    _share(ask, "fin", give=True)
    land = ready.TYPE_QUESTIONS["land_record"]
    items = ask.org.people["plain"].client.get("/api/chat/ready-questions").json()["items"]
    assert land in [item["text"] for item in items]  # its answer is made after the reply
    made = len(ask.api.answers.asked)
    _share(ask, "fin", give=False)
    _post(ask, "plain", {"question": land})  # Home asked it at the level it may (_home_inputs)
    assert len(ask.api.answers.asked) == made + 1
