"""#617 part 1 against PostgreSQL, as the app role under row level security: the word search's
bounded candidates (``kaagaz_word_candidates``, migration 0094), the per-organisation word counts
it reads (kaagaz/search/word_counts.py), and Gat, Survey, Khata and Plot numbers by exact lookup.
Customer separation: test_isolation_word_search.py."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

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


def counted(db: Database, customer: str, when: datetime = NOW) -> None:
    with db.customer(customer) as conn:
        word_counts.rebuild(conn, customer, when)


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
    """#617: one capped call a word column's query, joined by UNION, the glossary's by :gc."""
    word, sound = hybrid._WORD.text, hybrid._SOUND.text
    assert word.count("kaagaz_word_candidates(") == 3 and word.count(" UNION SELECT w.id") == 2
    assert "'tsv', websearch_to_tsquery('english', :q)" in word and "CAST(:gc AS text)" in word
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
            text(
                "SELECT field, n, avgdl, built_at FROM customer_stats WHERE customer_id = :c ORDER BY field"
            ),
            {"c": customer},
        ).all()
    assert terms["tsv:jindal"] == 2 and terms["tsv_simple:jindal"] == 2  # never the binned piece
    assert terms["tsv:paid"] == 1 and "tsv_simple:binned" not in terms
    assert [df for term, df in terms.items() if term.startswith("tsv_sound:bogvt")] == [1]
    assert [(field, n) for field, n, _, _ in stats] == [("tsv", 3), ("tsv_simple", 3), ("tsv_sound", 3)]
    assert all(avgdl > 0 and built_at == NOW for _, _, avgdl, built_at in stats)

    counted(db, customer)  # again: replaced, never added to
    with db.customer(customer) as conn:
        jindal = text("SELECT df FROM term_df WHERE customer_id = :c AND field = 'tsv' AND term = 'jindal'")
        again = conn.execute(jindal, {"c": customer}).scalar_one()
    assert again == 2


def test_counts_are_due_when_missing_a_day_old_or_the_pieces_changed_by_5_percent(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"Jindal {n}", None) for n in range(40)])

    def due(when: datetime = NOW) -> bool:
        with db.customer(customer) as conn:
            return word_counts.due(conn, customer, when)

    assert due()
    counted(db, customer)
    assert not due() and not due(NOW + timedelta(hours=23))
    assert due(NOW + timedelta(hours=24))
    add_pieces(db, customer, add_document(db, customer), [("Patil", None)])  # 1 of 40: 2.5%
    assert not due()
    add_pieces(db, customer, add_document(db, customer), [("Patil", None)])  # 2 of 40: 5%
    assert due()


def test_the_worker_rebuilds_only_the_organisations_due(db: Database) -> None:
    fresh, _ = new_customer(db)
    add_pieces(db, fresh, add_document(db, fresh), [("Jindal", None)])
    counted(db, fresh)
    stale, _ = new_customer(db)
    add_pieces(db, stale, add_document(db, stale), [("Patil", None)])

    rebuilt = rebuild_word_counts(db, NOW + timedelta(minutes=5))

    assert rebuilt >= 1
    with db.customer(stale) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM customer_stats WHERE customer_id = :c"), {"c": stale}
            ).scalar_one()
            == 3
        )
    with db.customer(fresh) as conn:
        built = conn.execute(
            text("SELECT min(built_at) FROM customer_stats WHERE customer_id = :c"), {"c": fresh}
        ).scalar_one()
    assert built == NOW  # not due: left as it was


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
    # Every sound word common: no sound rung (#617 item 2); "paatil" is Patil by sound only.
    assert word_list(db, customer, "paatil") == []


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

    def rare() -> list[str]:
        with db.customer(customer) as conn:
            return hybrid._rare(conn, customer, "tsv_simple", alternatives)

    assert rare() == alternatives  # the control: no counts, so the cap takes the first matches
    counted(db, customer)

    assert rare() == ["मालक:*"]  # खातेदार is on no piece
    assert word_list(db, customer, "owner") == [owner]


def test_when_every_glossary_word_is_common_they_all_still_bring_candidates(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [(f"भोगवटादार {n}", None) for n in range(10)])
    monkeypatch.setattr(hybrid, "CANDIDATE_CAP", 4)
    counted(db, customer)

    assert 0 < len(word_list(db, customer, "owner")) <= 4


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
def test_a_survey_number_in_either_script_finds_its_stored_value(db: Database, question: str) -> None:
    customer, _ = new_customer(db)
    _, wanted = _land(db, customer, "६१")
    add_pieces(db, customer, add_document(db, customer), [("Paid Rs 61 for gat stamps गट ६१ नाही", None)])

    assert word_list(db, customer, question) == [wanted]


def test_the_number_is_never_a_word_or_sound_term_once_a_value_holds_it(db: Database) -> None:
    """The other words are searched in the property's documents only; 61 itself is matched by
    equality on the stored value, so a piece elsewhere holding "61" or "area" is not listed."""
    customer, _ = new_customer(db)
    document = add_land(db, customer, "7-12.pdf", survey="गट नं. ६१")
    on_page, later = add_pieces(
        db, customer, document, [(LAND_TEXT, None), ("Area of the plot in the map", None)]
    )
    elsewhere = add_pieces(
        db, customer, add_document(db, customer), [("area 61", None), ("gat 61 area", None)]
    )

    found = word_list(db, customer, "area of gat 61")

    assert set(found) == {on_page, later} and not set(elsewhere) & set(found)
    assert word_list(db, customer, "क्षेत्र गट ६१")[0] == on_page  # the piece the words matched first


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
    assert word_list(db, customer, "survey no. 45") != [wanted]  # equality, never a prefix


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
