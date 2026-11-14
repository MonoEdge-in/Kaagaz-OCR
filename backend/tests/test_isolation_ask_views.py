"""PRD 7 PR 8 (#602): Ask and search read only what the person can see.

PR 6's walled organisation. Five of its files get the same content (``_ask_org``): fin, sal and
held are hidden from the plain reviewer, sales and given are not; Finance's member sees fin, held
and given but not sal. Each file has two pieces: the first holds a word of its own and "ledger",
with a vector of its own (one axis each); and a section, a topic, a card and the reviewer's own
story. Every Ask and search statement is called as the person, so it is proved by itself: nothing
of a hidden file, not as a row, a count, a weight or a slot before a LIMIT, and the member still
finds fin."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Connection, event, text

from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import FusedPiece
from kaagaz.search.scope import EVERYTHING
from kaagaz.search.story_search import read_stories
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
