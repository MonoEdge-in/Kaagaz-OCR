"""#617 part 1 against PostgreSQL, as the app role under row level security: the word search's
bounded candidates (``kaagaz_word_candidates``, migration 0094), the per-organisation word counts
it reads (kaagaz/search/word_counts.py), and Gat, Survey, Khata and Plot numbers by exact lookup.
Customer separation: test_isolation_word_search.py."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from kaagaz import worker_main
from kaagaz.answering.expansion import NO_EXPANSION
from kaagaz.db.engine import Database
from kaagaz.search import hybrid, word_counts
from kaagaz.search.scope import EVERYTHING, Scope
from kaagaz.worker_main import rebuild_word_counts
from tests.db_support import insert_document, insert_folder, new_customer
from tests.land_support import add_land
from tests.test_search_db import add_document, add_pieces, run

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 15, 10, tzinfo=UTC)
_CANDIDATES = text(
    "SELECT kaagaz_word_candidates(:column, to_tsquery('simple', :q), CAST(:docs AS uuid[]), :cap)"
)


def candidates(
    db: Database,
    customer: str,
    q: str,
    *,
    column: str = "tsv_simple",
    docs: list[str] | None = None,
    cap: int = 10,
) -> list[str]:
    """The function's ids for ``q``, as the app role with ``customer`` set."""
    with db.customer(customer) as conn:
        params = {"column": column, "q": q, "docs": docs, "cap": cap}
        return [str(row[0]) for row in conn.execute(_CANDIDATES, params)]


def word_list(db: Database, customer: str, question: str, scope: Scope = EVERYTHING) -> list[str]:
    with db.customer(customer) as conn:
        conn.execute(hybrid._SET_SEARCH, {"ms": str(hybrid.STATEMENT_TIMEOUT_MS)})
        rows = hybrid._word_list(conn, customer, question, scope, NO_EXPANSION)
    return [str(row[0]) for row in rows]


def counted(db: Database, customer: str, when: datetime = NOW, most: int = 0) -> None:
    """The organisation's word counts rebuilt; ``most`` 0, so its words are counted however few."""
    with db.customer(customer) as conn:
        word_counts.rebuild(conn, customer, when, most)


def built(db: Database, customer: str) -> tuple[object, ...]:
    """(rows of term_df, min(built_at) and min(n) of customer_stats) of the organisation."""
    with db.customer(customer) as conn:
        statement = text(
            "SELECT (SELECT count(*) FROM term_df WHERE customer_id = :c),"
            " (SELECT min(built_at) FROM customer_stats WHERE customer_id = :c),"
            " (SELECT min(n) FROM customer_stats WHERE customer_id = :c)"
        )
        return tuple(conn.execute(statement, {"c": customer}).one())


# --- the candidates ---------------------------------------------------------------------


def test_the_candidates_stop_at_the_cap_and_never_pass_2000(db: Database) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [(f"Sharma invoice {n}", None) for n in range(2010)])

    assert len(candidates(db, customer, "sharma", cap=7)) == 7
    assert len(candidates(db, customer, "sharma", cap=5000)) == 2000  # the function's own ceiling
    assert candidates(db, customer, "sharma", cap=-1) == []
    assert len(set(candidates(db, customer, "sharma", cap=2000))) == 2000  # each piece once


@pytest.mark.parametrize("column", ["tsv", "tsv_simple", "tsv_sound"])
def test_each_word_column_has_its_own_query(db: Database, column: str) -> None:
    customer, _ = new_customer(db)
    texts = {"tsv": "invoices paid", "tsv_simple": "invoices paid", "tsv_sound": "भोगवटादार"}
    query = {"tsv": "invoic", "tsv_simple": "invoices", "tsv_sound": "bogvtdr:*"}[column]
    wanted, _ = add_pieces(db, customer, add_document(db, customer), [(texts[column], None), ("Rent", None)])

    assert candidates(db, customer, query, column=column) == [wanted]
    with pytest.raises(Exception, match="unknown word column"), db.customer(customer) as conn:
        conn.execute(_CANDIDATES, {"column": "text", "q": "rent", "docs": None, "cap": 10})


def test_the_candidates_are_live_pieces_inside_the_documents_given(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        old = insert_document(conn, customer, is_current=False)
        current = insert_document(conn, customer, family_id=old, version_no=2)
        binned = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)
        reading = insert_document(conn, customer, status="clean")
        other = insert_document(conn, customer)
    pieces = {
        d: add_pieces(db, customer, d, [("Jindal", None)])[0] for d in (old, current, binned, reading, other)
    }

    assert set(candidates(db, customer, "jindal")) == {pieces[current], pieces[other]}
    assert candidates(db, customer, "jindal", docs=[current, binned]) == [pieces[current]]
    assert candidates(db, customer, "jindal", docs=[]) == []


def test_the_word_statements_take_each_query_on_its_own_never_one_or() -> None:
    """#617: one capped call a word column's query, joined by UNION; the glossary's rare (:gc)
    and common (:gk) alternatives each a call of their own."""
    word, sound = hybrid._WORD.text, hybrid._SOUND.text
    assert word.count("kaagaz_word_candidates(") == 4 and word.count(" UNION SELECT w.id") == 3
    assert "'tsv', websearch_to_tsquery('english', :q)" in word
    assert "CAST(:gc AS text)" in word and "CAST(:gk AS text)" in word
    assert sound.count("kaagaz_word_candidates('tsv_sound'") == 1 and "CAST(:skc AS text)" in sound
    assert all(":cap" in statement and ":docs" in statement for statement in (word, sound))


def test_a_word_list_reads_at_most_the_cap_a_query(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [(f"Sharma {n}", None) for n in range(12)])
    assert len(word_list(db, customer, "sharma")) == 12  # the control

    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 3)

    assert 0 < len(word_list(db, customer, "sharma")) <= 3 * 3  # two exact queries and the sound rung's


def test_a_folder_scope_takes_its_candidates_from_its_own_documents(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap never crowds out the scope: with a folder, its documents are listed first."""
    customer, user = new_customer(db)
    for _ in range(4):
        add_pieces(db, customer, add_document(db, customer), [("Jindal outside", None)])
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Land")
        inside = insert_document(conn, customer, folder_id=folder)
    (wanted,) = add_pieces(db, customer, inside, [("Jindal inside", None)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 2)

    assert word_list(db, customer, "jindal", Scope(folder_ids=(folder,))) == [wanted]
    assert word_list(db, customer, "jindal", Scope(folder_ids=())) == []


def test_a_scope_of_too_many_documents_is_left_to_the_statement(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #617 part 1: past SCOPE_DOCUMENTS_MOST the list is not passed (None); the word
    statement still applies the scope, so nothing outside it is found."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Land")
        inside = [insert_document(conn, customer, folder_id=folder) for _ in range(2)]
    wanted = [add_pieces(db, customer, d, [("Jindal inside", None)])[0] for d in inside]
    add_pieces(db, customer, add_document(db, customer), [("Jindal outside", None)])
    scope = Scope(folder_ids=(folder,))
    with db.customer(customer) as conn:
        assert sorted(hybrid._candidate_documents(conn, {"c": customer} | scope.params()) or []) == sorted(
            inside
        )
        monkeypatch.setattr(hybrid, "SCOPE_DOCUMENTS_MOST", 1)
        assert hybrid._candidate_documents(conn, {"c": customer} | scope.params()) is None

    assert sorted(word_list(db, customer, "jindal", scope)) == sorted(wanted)


# --- word counts ----------------------------------------------------------------------


def test_the_counts_are_the_live_pieces_words_per_column(db: Database) -> None:
    customer, user = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("Jindal paid", None), ("Jindal rent", None), ("भोगवटादार", None)])
    with db.customer(customer) as conn:
        binned = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)
    add_pieces(db, customer, binned, [("Jindal binned", None)])

    counted(db, customer)

    with db.customer(customer) as conn:
        listed = text("SELECT field || ':' || term, df FROM term_df WHERE customer_id = :c")
        rows = conn.execute(listed, {"c": customer})
        terms = {str(row[0]): int(row[1]) for row in rows}
        stats = conn.execute(
            text("SELECT field, n, built_at FROM customer_stats WHERE customer_id = :c ORDER BY field"),
            {"c": customer},
        ).all()
    assert terms["tsv_simple:jindal"] == 2 and terms["tsv_simple:paid"] == 1  # never the binned piece
    assert "tsv_simple:binned" not in terms and not any(term.startswith("tsv:") for term in terms)
    assert [df for term, df in terms.items() if term.startswith("tsv_sound:bogvt")] == [1]
    assert [tuple(row) for row in stats] == [("tsv_simple", 3, NOW), ("tsv_sound", 3, NOW)]

    counted(db, customer)  # again: replaced, never added to
    with db.customer(customer) as conn:
        jindal = text(
            "SELECT df FROM term_df WHERE customer_id = :c AND field = 'tsv_simple' AND term = 'jindal'"
        )
        again = conn.execute(jindal, {"c": customer}).scalar_one()
    assert again == 2


def test_an_organisation_with_few_pieces_gets_its_count_only(db: Database) -> None:
    """Review of #617 part 1: with no more live pieces than the cap, no word is ever common, so
    only the count is written."""
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("Jindal paid", None), ("Jindal rent", None)])

    counted(db, customer, most=hybrid.CANDIDATE_CAP)

    assert built(db, customer) == (0, NOW, 2)


def test_counts_are_due_when_missing_a_day_old_or_the_pieces_changed_by_5_percent(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"Jindal {n}", None) for n in range(40)])

    def due(when: datetime = NOW) -> bool:
        with db.customer(customer) as conn:
            return word_counts.due(conn, customer, when)[0]

    assert due()
    counted(db, customer)
    assert not due() and not due(NOW + timedelta(hours=23))
    assert not due(NOW + timedelta(hours=24))  # a day old, but as many live pieces: kept
    add_pieces(db, customer, add_document(db, customer), [("Patil", None)])  # 1 of 40: 2.5%
    assert not due() and due(NOW + timedelta(hours=24))  # changed a little: nightly
    add_pieces(db, customer, add_document(db, customer), [("Patil", None)])  # 2 of 40: 5%
    assert due()


def test_the_worker_rebuilds_only_the_organisations_due(db: Database) -> None:
    fresh, _ = new_customer(db)
    add_pieces(db, fresh, add_document(db, fresh), [("Jindal", None)])
    counted(db, fresh)
    stale, _ = new_customer(db)
    add_pieces(db, stale, add_document(db, stale), [("Patil", None)])

    rebuilt = rebuild_word_counts(db, NOW + timedelta(minutes=5), lambda: 0.0)

    assert rebuilt >= 1
    assert built(db, stale)[1:] == (NOW + timedelta(minutes=5), 1)
    assert built(db, fresh)[1] == NOW  # not due: left as it was


def test_one_organisation_failing_never_stops_the_others(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review of #617 part 1: the first organisation's rebuild fails; it is logged with its id
    and error class, and the rest are rebuilt."""
    first, _ = new_customer(db)
    second, _ = new_customer(db)
    for customer in (first, second):
        add_pieces(db, customer, add_document(db, customer), [("Jindal", None)])
    real = word_counts.rebuild

    def failing(conn: Connection, customer: str, now: datetime, most: int) -> None:
        if customer == first:
            raise ProgrammingError("SELECT", {}, Exception("broken"))
        real(conn, customer, now, most)

    monkeypatch.setattr(word_counts, "rebuild", failing)
    caplog.set_level("ERROR", logger="kaagaz.worker")

    rebuild_word_counts(db, NOW, lambda: 0.0)

    assert built(db, first)[1] is None and built(db, second)[1] == NOW
    failed = [r for r in caplog.records if r.getMessage() == "word counts not rebuilt"]
    assert [(getattr(r, "customer_id", None), getattr(r, "code", None)) for r in failed] == [
        (first, "ProgrammingError")
    ]


def test_a_database_that_is_down_stops_the_rebuilds(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost connection (an OperationalError with no SQLSTATE) is raised, for the hourly turn."""
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("Jindal", None)])

    def down(conn: Connection, customer: str, now: datetime, most: int) -> None:
        raise OperationalError("SELECT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(word_counts, "rebuild", down)

    with pytest.raises(OperationalError):
        rebuild_word_counts(db, NOW, lambda: 0.0)


def test_the_never_counted_go_first_and_a_turn_stops_after_its_time(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #617 part 1: with no time left after the first rebuild, that one is a never
    counted organisation, never one whose counts are only old."""
    old, _ = new_customer(db)
    add_pieces(db, old, add_document(db, old), [(f"Jindal {n}", None) for n in range(10)])
    counted(db, old, NOW - timedelta(days=2))
    add_pieces(db, old, add_document(db, old), [("Patil", None)])  # changed: due
    fresh, _ = new_customer(db)
    add_pieces(db, fresh, add_document(db, fresh), [("Jindal", None)])
    monkeypatch.setattr(worker_main, "WORD_COUNTS_SECONDS", 0.0)

    assert rebuild_word_counts(db, NOW, lambda: 0.0) == 1
    assert built(db, old)[1] == NOW - timedelta(days=2)


def test_shares_read_prefixes_as_sums_and_are_none_without_counts(db: Database) -> None:
    customer, _ = new_customer(db)
    words = [*(f"jamin {n}" for n in range(6)), "jamini", "Patil", "rent"]
    pieces: list[tuple[str, list[float] | None]] = [(word, None) for word in words]
    add_pieces(db, customer, add_document(db, customer), pieces)

    def shares(alternatives: list[list[str]], most: int = 2) -> list[float] | None:
        with db.customer(customer) as conn:
            return word_counts.shares(conn, customer, "tsv_simple", alternatives, most)

    assert shares([["jamin"], ["patil"]]) is None  # no counts yet
    counted(db, customer)
    # 9 pieces: jamin on 6, jamin:* on 7 (jamini too), patil on 1, patil or rent on 2.
    assert shares([["jamin"], ["jamin:*"], ["patil"], ["patil", "rent"], ["absent"], []]) == [
        6 / 9, 7 / 9, 1 / 9, 2 / 9, 0.0, 0.0,
    ]  # fmt: skip
    assert shares([["jamin"]], most=9) is None  # no more pieces than the cap: no shares


# --- only rare words bring candidates -------------------------------------------------


def test_a_common_sound_word_only_scores(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """#617: with counts, Patil (on most pieces) brings no candidates by sound; the rare
    Gaikwad does, and Patil still weighs the piece holding both first."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    common = [(f"पाटील {n}", None) for n in range(10)]
    *_, alone, both = add_pieces(
        db, customer, document, [*common, ("गायकवाड", None), ("पाटील गायकवाड", None)]
    )
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    assert len(word_list(db, customer, "patil gaikwad")) > 2  # the control: no counts, every word

    counted(db, customer)

    assert word_list(db, customer, "patil gaikwad") == [both, alone]
    # One sound word, common: no sound rung (#617 item 2); "paatil" is Patil by sound only.
    assert word_list(db, customer, "paatil") == []


def test_two_common_sound_words_are_searched_together(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #617 part 1: no word rare, but two of them: their keys ANDed bring candidates."""
    customer, _ = new_customer(db)
    pieces = [(f"Krishna {n}", None) for n in range(6)] + [(f"Patil {n}", None) for n in range(6)]
    *_, first, second = add_pieces(
        db, customer, add_document(db, customer), [*pieces, ("Krishna Patil", None), ("Patil Krishna", None)]
    )
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)

    assert sorted(word_list(db, customer, "krushna pateel")) == sorted([first, second])


def test_a_word_is_judged_by_its_own_key(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #617 part 1: "krushnarao"'s own key is rare, though its start (Krishna's) is on
    most pieces; it brings its piece by its rare terms only, never crowded out by Krishna's."""
    customer, _ = new_customer(db)
    pieces = [(f"Krishna {n}", None) for n in range(10)]
    *_, wanted = add_pieces(db, customer, add_document(db, customer), [*pieces, ("Krishnarao Jadhav", None)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)

    assert word_list(db, customer, "krushnarao") == [wanted]


def test_a_word_new_since_the_counts_brings_its_piece(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #617 part 1: a word on no piece when counted (share 0) is rare, by sound and in
    the glossary, so a piece added after the rebuild is found before the next one."""
    customer, _ = new_customer(db)
    common: list[tuple[str, list[float] | None]] = [(f"भोगवटादार पाटील {n}", None) for n in range(10)]
    add_pieces(db, customer, add_document(db, customer), common)
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)
    owner, named = add_pieces(
        db, customer, add_document(db, customer), [("मालक राम", None), ("गायकवाड", None)]
    )

    assert owner in word_list(db, customer, "owner")
    assert word_list(db, customer, "gaikwad") == [named]


def test_a_rare_glossary_word_is_never_crowded_out_by_a_common_one(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "owner" is भोगवटादार, मालक or खातेदार: भोगवटादार is on most pieces, so only मालक and
    खातेदार bring candidates. With no counts the cap takes the first भोगवटादार pieces instead."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    common = [(f"भोगवटादार {n}", None) for n in range(10)]
    *_, owner = add_pieces(db, customer, document, [*common, ("मालक रामचंद्र", None)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    alternatives = ["भोगवटादार:*", "मालक:*", "खातेदार:*"]  # glossary.word_query(["owner"])

    def generated() -> tuple[str, str]:
        with db.customer(customer) as conn:
            return hybrid._glossary_candidates(conn, {"c": customer, "g": " | ".join(alternatives)}, None)

    assert generated() == (" | ".join(alternatives), "")  # the control: no counts, one call
    counted(db, customer)

    assert generated() == ("मालक:* | खातेदार:*", "")  # मालक is held, so the common call is left out
    assert word_list(db, customer, "owner") == [owner]


def test_when_every_glossary_word_is_common_they_all_still_bring_candidates(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [(f"भोगवटादार {n}", None) for n in range(10)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)

    assert 0 < len(word_list(db, customer, "owner")) <= 4  # भोगवटादार, by the common call (:gk)


def test_inside_a_scope_a_common_glossary_word_still_brings_its_piece(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #617 part 1: a scope's documents already bound the candidates, so every
    alternative brings them there: भोगवटादार is common and मालक is held, yet the folder's
    भोगवटादार piece is found."""
    customer, user = new_customer(db)
    outside: list[tuple[str, list[float] | None]] = [(f"भोगवटादार {n}", None) for n in range(10)]
    outside.append(("मालक राम", None))
    add_pieces(db, customer, add_document(db, customer), outside)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Land")
        inside = insert_document(conn, customer, folder_id=folder)
    (wanted,) = add_pieces(db, customer, inside, [("भोगवटादार रामचंद्र", None)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)

    assert word_list(db, customer, "owner", Scope(folder_ids=(folder,))) == [wanted]


# --- identifiers by exact lookup (#617 item 9) ----------------------------------------

LAND_TEXT = "गाव नमुना सात बारा गट नंबर ६१ भोगवटादार रामचंद्र पाटील क्षेत्र 1.20 हेक्टर"


def _land(db: Database, customer: str, survey: str, text_: str = LAND_TEXT) -> tuple[str, str]:
    """A land record whose survey number is stored (value_keys and its value, page 1), with one
    piece on that page; (document, piece)."""
    document = add_land(db, customer, "7-12.pdf", survey=survey)
    (piece,) = add_pieces(db, customer, document, [(text_, None)])
    return document, piece


@pytest.mark.parametrize(
    "question",
    ["gat 61", "Gat No. 61", "gut number 61", "survey no 61", "गट नं. ६१", "गट नंबर ६१", "सर्व्हे ६१", "s.no. 61"],
)
def test_a_survey_number_in_either_script_finds_its_stored_value_first(db: Database, question: str) -> None:
    customer, _ = new_customer(db)
    _, wanted = _land(db, customer, "६१")
    add_pieces(db, customer, add_document(db, customer), [("Paid Rs 61 for gat stamps", None)])

    assert word_list(db, customer, question)[0] == wanted


def test_the_stored_property_comes_first_then_the_words_as_typed(db: Database) -> None:
    """The other words are searched in the property's documents first, the stored value giving 61;
    then the question as typed over the whole scope: "gat 61 area" elsewhere comes next, and
    "area 61" (no gat) only after it, by sound."""
    customer, _ = new_customer(db)
    document = add_land(db, customer, "7-12.pdf", survey="गट नं. ६१")
    on_page, later = add_pieces(
        db, customer, document, [(LAND_TEXT, None), ("Area of the plot in the map", None)]
    )
    bare, labelled = add_pieces(
        db, customer, add_document(db, customer), [("area 61", None), ("gat 61 area", None)]
    )

    found = word_list(db, customer, "area of gat 61")

    assert set(found[:2]) == {on_page, later} and found[2] == labelled and bare not in found[:3]
    assert word_list(db, customer, "क्षेत्र गट ६१")[0] == on_page  # the piece the words matched first


def test_a_document_without_the_stored_value_is_found_after_one_with_it(db: Database) -> None:
    """Review of #617 part 1: one document has the value stored, another only prints it; both."""
    customer, _ = new_customer(db)
    _, stored = _land(db, customer, "61")
    (printed,) = add_pieces(db, customer, add_document(db, customer), [("Gut No. 61, Kusgaon", None)])

    assert word_list(db, customer, "gat 61") == [stored, printed]


def test_each_number_is_looked_up_with_its_own_kind(db: Database) -> None:
    """Review of #617 part 1: "gat 61 khata 12" is survey 61 and khata 12, never survey 12."""
    customer, _ = new_customer(db)
    survey_61 = add_land(db, customer, "a.pdf", survey="61")
    survey_12 = add_land(db, customer, "b.pdf", survey="12")
    khata_12 = add_land(db, customer, "c.pdf", printed=[("khata_number", "12")])
    pieces = {d: add_pieces(db, customer, d, [("उतारा", None)])[0] for d in (survey_61, survey_12, khata_12)}

    found = word_list(db, customer, "gat 61 khata 12")

    assert set(found) == {pieces[survey_61], pieces[khata_12]}


def test_a_card_identifier_is_found_too(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    (wanted,) = add_pieces(db, customer, document, [("Sale deed of the land at Kusgaon", None)])
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
                " value_raw, page_no, region, reader, label, label_reason)"
                " VALUES (:c, :d, 'card', 'survey_number', 'Gat No. 45/2', 'Gat No. 45/2', 1,"
                " CAST(:r AS jsonb), 'test', 'verified', 'readers_agree')"
            ),
            {"c": customer, "d": document, "r": '{"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]}'},
        )

    assert word_list(db, customer, "survey no. 45/2") == [wanted]
    assert word_list(db, customer, "survey no. 45") == []  # the whole number, never its start


def test_the_kind_must_match_khata_is_not_survey(db: Database) -> None:
    customer, _ = new_customer(db)
    _, survey = _land(db, customer, "61")

    assert word_list(db, customer, "khata 61") == []  # no stored khata 61: its words, which no piece holds
    assert word_list(db, customer, "gat 61") == [survey]


def test_no_stored_value_searches_the_words_as_typed(db: Database) -> None:
    """A land record read before its values were stored still answers "gat 61" by its words
    (#625's test_gut_and_gat_find_each_other)."""
    customer, _ = new_customer(db)
    (wanted, _) = add_pieces(
        db, customer, add_document(db, customer), [("Gut No. 61, Kusgaon", None), ("Rent", None)]
    )

    assert word_list(db, customer, "gat 61") == [wanted]
    assert [hit.piece_id for hit in run(db, customer, "gat 61")] == [wanted]


def test_identifiers_are_read_with_their_kind_and_never_from_a_bare_number() -> None:
    survey = ("land_survey_number", "survey_number")
    khata = ("land_khata_number", "khata_number")
    said = {
        "gat 61": [(survey, "61")],
        "Gut No. 61 and khata no 12": [(survey, "61"), (khata, "12")],
        "Survey No: 45 / 2": [(survey, "45/2")],
        "गट नं. ६१": [(survey, "61")],
        "खाते क्रमांक १२": [(khata, "12")],
        "plot 7-A": [(("land_plot_number",), "7")],
        # As chat searches Devanagari (normalise's Latin spelling): गट नं. ६१, सर्व्हे नं ४५/२, खाते क्रमांक १२.
        "gat nan 61": [(survey, "61")],
        "sarvhe nan 45/2": [(survey, "45/2")],
        "khate kramank 12": [(khata, "12")],
        "plot nambar 7": [(("land_plot_number",), "7")],
        # Review of #617 part 1: Sy. No., ग.नं. and khasra (खसरा).
        "Sy. No. 12": [(survey, "12")],
        "ग.नं. ६१": [(survey, "61")],
        "khasra 45": [(survey, "45")],
        "खसरा ४५": [(survey, "45")],
        "61": [],
        "invoice 61": [],
        "gateway 61": [],  # a word that starts like gat is not gat
    }
    for question, expected in said.items():
        assert [(fields, number) for fields, number, _ in hybrid.identifiers(question)] == expected, question


def test_a_scope_keeps_its_identifiers_too(db: Database) -> None:
    customer, _ = new_customer(db)
    first, _ = _land(db, customer, "61")
    second, wanted = _land(db, customer, "61")

    assert word_list(db, customer, "gat 61", Scope(document_ids=(second,))) == [wanted]
    assert word_list(db, customer, "gat 61", Scope(document_ids=(str(uuid.uuid4()),))) == []
    assert first != second
