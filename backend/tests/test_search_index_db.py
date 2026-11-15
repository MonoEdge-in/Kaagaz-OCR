"""The meaning index (PRD 3 V2) against a real PostgreSQL with pgvector 0.8.

The recall cases get a fresh database, so the HNSW graph and the statistics hold
only their data: 20 clusters of unit vectors for a big (95%) and a small (5%)
customer, and held-out questions from the same clusters, made from blake2b of a
counter (the same every run). The truth is an exact scan of the same column.
"""

import hashlib
import math
import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from kaagaz.db.engine import Database, normalise_url
from kaagaz.search import hybrid
from kaagaz.search.hybrid import CANDIDATES, search, vector_literal
from kaagaz.search.scope import EVERYTHING
from tests import test_migration_0002_db as m0002
from tests.db_support import APP_ROLE, database_url, make_database, migrate, new_customer
from tests.test_api_errors import _client_raising
from tests.test_search_db import EF_SEARCH, RECIPE, add_document, add_pieces, axis, run

pytestmark = pytest.mark.db

DIM = 1024
SEED = 20261002
CENTRES = 20
NOISE = 0.05  # times a number in [-1, 1], added to each of a centre's numbers
BIG, SMALL = 1900, 100  # 2,000 vectors, as PRD 3 V2 asks
QUESTIONS = 30  # 20 for the big customer, 10 for the small one
_EXACT = text(
    "SELECT p.id::text FROM pieces p WHERE p.customer_id = :c AND p.embedding_model = :recipe"
    " ORDER BY p.embedding <=> CAST(:vec AS halfvec(1024)), p.id LIMIT :n"
)


@dataclass(frozen=True, slots=True)
class Clusters:
    db: Database
    big: str
    small: str
    questions: list[tuple[str, list[float]]]  # (customer, vector)


def _numbers(seed: str, count: int) -> list[float]:
    """``count`` numbers in [-1, 1], the same for the same seed. O(count)."""
    out: list[float] = []
    block = 0
    while len(out) < count:
        out.extend((byte - 127.5) / 127.5 for byte in hashlib.blake2b(f"{seed}:{block}".encode()).digest())
        block += 1
    return out[:count]


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [round(value / norm, 4) for value in values]


@pytest.fixture(scope="module")
def clusters() -> Iterator[Clusters]:
    main = make_url(database_url())
    name = f"{main.database}_recall_{os.getpid()}"  # this run's own (QA W5)
    url = main.set(database=name).render_as_string(hide_password=False)
    m0002._recreate(database_url(), name, create=True)
    database = make_database(url)
    try:
        migrate(url, "head")
        centres = [_unit(_numbers(f"{SEED}:centre:{k}", DIM)) for k in range(CENTRES)]
        drawn = 0

        def draw() -> list[float]:
            nonlocal drawn
            drawn += 1
            centre = centres[int(_numbers(f"{SEED}:pick:{drawn}", 1)[0] * 1000) % CENTRES]
            noise = _numbers(f"{SEED}:noise:{drawn}", DIM)
            return _unit([value + NOISE * extra for value, extra in zip(centre, noise, strict=True)])

        big, _ = new_customer(database)
        small, _ = new_customer(database)
        for customer, count in ((big, BIG), (small, SMALL)):
            document = add_document(database, customer)
            add_pieces(database, customer, document, [(f"piece {n}", draw()) for n in range(count)])
        # Only the table owner may ANALYZE (the app role may not); it reads no rows into the test.
        with database.engine.begin() as conn:
            conn.execute(text("ANALYZE pieces, documents"))
        questions = [(big if n < 20 else small, draw()) for n in range(QUESTIONS)]
        yield Clusters(database, big, small, questions)
    finally:
        database.engine.dispose()
        m0002._recreate(database_url(), name, create=False)


def _search(conn: Connection, customer: str, vector: list[float], limit: int, query: str = "") -> list[str]:
    hits = search(
        conn,
        customer_id=customer,
        query_text=query,
        embedding=vector,
        limit=limit,
        recipe=RECIPE,
        ef_search=EF_SEARCH,
    )
    return [hit.piece_id for hit in hits]


def _exact(db: Database, customer: str, vector: list[float], limit: int) -> list[str]:
    with db.customer(customer) as conn:
        conn.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
        params = {"c": customer, "recipe": RECIPE, "vec": vector_literal(vector), "n": limit}
        return [str(row[0]) for row in conn.execute(_EXACT, params)]


def _plan(conn: Connection, statement: str, customer: str, vector: list[float]) -> str:
    params = {"c": customer, "vec": vector_literal(vector), "recipe": RECIPE, "n": CANDIDATES}
    params |= EVERYTHING.params()
    return "\n".join(conn.execute(text("EXPLAIN " + statement), params).scalars())


def test_the_index_keeps_at_least_90_percent_of_the_exact_top_10(clusters: Clusters) -> None:
    overlaps = []
    for customer, vector in clusters.questions:
        with clusters.db.customer(customer) as conn:
            found = _search(conn, customer, vector, 10)
        exact = _exact(clusters.db, customer, vector, 10)
        assert len(found) == len(exact) == 10
        overlaps.append(len(set(found) & set(exact)) / 10)

    assert len(overlaps) >= 30
    assert sum(overlaps) / len(overlaps) >= 0.9


def test_explain_of_the_production_statement_uses_pieces_embedding(clusters: Clusters) -> None:
    customer, vector = clusters.questions[0]
    assert customer == clusters.big
    with clusters.db.customer(customer) as conn:
        for _ in range(12):  # psycopg prepares on the 6th run; PostgreSQL plans 5 custom first
            _search(conn, customer, vector, 10)
        assert "Index Scan using pieces_embedding" in _plan(conn, hybrid._MEANING.text, customer, vector)
        assert "pieces_embedding" not in _plan(conn, hybrid._MEANING_EXACT.text, customer, vector)
        # Once prepared, every run is still planned with its own values: the generic
        # plan (no customer, no vector known) is the exact scan.
        plans = conn.execute(
            text(
                "SELECT generic_plans, custom_plans FROM pg_prepared_statements"
                " WHERE statement LIKE '%ORDER BY distance + 0, id'"
            )
        ).all()
    assert len(plans) == 1 and plans[0][0] == 0 and plans[0][1] >= 1


def test_a_short_index_walk_falls_back_to_the_exact_scan(clusters: Clusters) -> None:
    """The small customer, with the walk cut to 130 tuples (pgvector allows 1 and up)
    and the planner made to take the index (no sort): about 5% of them are its own."""
    customer, vector = clusters.questions[-1]
    assert customer == clusters.small
    exact = _exact(clusters.db, customer, vector, CANDIDATES)
    with clusters.db.customer(customer) as conn:
        conn.execute(text("SELECT set_config('hnsw.max_scan_tuples', '130', true)"))
        conn.execute(text("SELECT set_config('enable_sort', 'off', true)"))
        found = _search(conn, customer, vector, CANDIDATES)
        # The index walk alone, with the settings search() just set:
        assert "pieces_embedding" in _plan(conn, hybrid._MEANING.text, customer, vector)
        params = {"c": customer, "vec": vector_literal(vector), "recipe": RECIPE, "n": CANDIDATES}
        short = conn.execute(hybrid._MEANING, params | EVERYTHING.params()).all()

    assert len(short) < CANDIDATES  # without the fallback: too few rows
    assert len(found) == len(exact) == CANDIDATES
    assert found == exact  # with it: recall 1.0


def test_the_search_settings_end_with_their_transaction(db: Database) -> None:
    """One pooled connection, two transactions: the second sees the defaults again."""
    engine = create_engine(normalise_url(database_url()), pool_size=1, max_overflow=0)
    pooled = Database(engine, APP_ROLE)
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("Invoice total", axis(0))])
    settings = text(
        "SELECT pg_backend_pid(), current_setting('hnsw.ef_search'), current_setting('hnsw.iterative_scan'),"
        " current_setting('statement_timeout'), current_setting('pg_trgm.word_similarity_threshold'),"
        " current_setting('plan_cache_mode')"
    )
    try:
        with pooled.customer(customer) as conn:
            assert len(_search(conn, customer, axis(0), 10, "invoice")) == 1
            during = conn.execute(settings).one()
        with pooled.customer(customer) as conn:
            after = conn.execute(settings).one()
    finally:
        engine.dispose()

    assert during[0] == after[0]  # the same connection
    assert tuple(during[1:]) == ("100", "relaxed_order", "3s", "0.3", "force_custom_plan")
    assert tuple(after[1:]) == ("40", "off", "0", "0.6", "auto")


def test_a_meaning_list_past_the_timeout_is_dropped_and_words_still_answer(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The meaning statement is swapped for one that sleeps past a short timeout."""
    customer, _ = new_customer(db)
    (piece,) = add_pieces(db, customer, add_document(db, customer), [("Invoice INV-3003", axis(0))])
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 200)
    monkeypatch.setattr(
        hybrid,
        "_MEANING",
        text(
            "SELECT p.id, 0.0 FROM pieces p CROSS JOIN pg_sleep(5)"
            " WHERE p.customer_id = :c AND :recipe <> '' AND :n > 0 AND length(:vec) > 0"
        ),
    )

    hits = run(db, customer, "INV-3003", axis(0))

    assert [hit.piece_id for hit in hits] == [piece]
    assert hits[0].word_score is not None and hits[0].meaning_score is None


@pytest.mark.parametrize("statement", ["_WORD", "_FUZZY"])
def test_a_word_or_typo_list_past_the_timeout_is_dropped_and_the_other_still_answers(
    db: Database, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    """Changed on purpose (#619, production 15 Nov 2026): a cancelled word or typo list is dropped
    like the meaning list, never a 503 for the whole question; the other list still answers. The
    API's handler still answers 503 to an OperationalError that reaches it."""
    customer, _ = new_customer(db)
    (piece,) = add_pieces(db, customer, add_document(db, customer), [("Invoice INV-3003", None)])
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 200)
    monkeypatch.setattr(
        hybrid,
        statement,
        text(
            # Changed on purpose (#617): the typo list binds no :k, only what _FUZZY reads.
            "SELECT p.id, 0.0 FROM pieces p CROSS JOIN pg_sleep(5)"
            " WHERE p.customer_id = :c AND :q <> '' AND :n > 0"
        ),
    )

    assert [hit.piece_id for hit in run(db, customer, "INV-3003")] == [piece]

    with pytest.raises(OperationalError) as cancelled, db.customer(customer) as conn:
        conn.execute(text("SELECT set_config('statement_timeout', '200', true)"))
        conn.execute(getattr(hybrid, statement), {"c": customer, "q": "x", "n": 1})
    assert getattr(cancelled.value.orig, "sqlstate", None) == "57014"
    response = _client_raising(cancelled.value).get("/api/boom")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "backend_unavailable")
