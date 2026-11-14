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
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Connection, event, text

from kaagaz.answering import exhaustive, fixed_queries, ready
from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, latin_key, picked_page
from kaagaz.answering.exhaustive import PgExhaustive
from kaagaz.answering.fixed_queries import PgFixedQueries
from kaagaz.answering.hard_to_read import PgReadingQuality
from kaagaz.answering.owners import OWNING, PgOwners
from kaagaz.answering.unchecked import PgUncheckedValues
from kaagaz.db import chat as chat_sql
from kaagaz.db import organization_words
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import FusedPiece
from kaagaz.search.scope import EVERYTHING
from kaagaz.search.story_search import read_stories
from tests.db_support import insert_key, insert_tag, tag_document
from tests.test_api_support import Api
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
    return AskOrg(db, org, pieces, axes)


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
    ("_WORD", lambda conn, a: _search(conn, a, WORDS["fin"])),
    ("_FUZZY", lambda conn, a: _search(conn, a, "aardvrk")),
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
        assert pins == ({ask.id("fin")} if sees_fin else set()), person
        assert learned == set(states), person


def test_a_word_made_on_a_hidden_page_is_never_used_or_echoed(db: Database, ask: AskOrg) -> None:
    """``approved`` for a scope of the hidden file answers as for a random id; the repeat key's
    words leave it out too, so it moves no plain reviewer's key."""
    _approved_word(ask, None, "everyone")
    with _as(db, ask, "plain") as conn:
        before = conn.execute(ready._WORDS, {"c": ask.org.c, "u": ask.user("plain")}).scalar_one()
    _approved_word(ask, ask.id("fin"), "secretword")
    for person, sees in (("plain", False), ("member", True)):
        with _as(db, ask, person) as conn:
            words = {w.word for w in organization_words.approved(conn, ask.org.c, document_id=ask.id("fin"))}
            random = {
                w.word for w in organization_words.approved(conn, ask.org.c, document_id=str(uuid.uuid4()))
            }
            after = conn.execute(ready._WORDS, {"c": ask.org.c, "u": ask.user(person)}).scalar_one()
        assert ("secretword" in words) == sees and random == {"everyone"}, person
        if not sees:
            assert words == random and after == before


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
