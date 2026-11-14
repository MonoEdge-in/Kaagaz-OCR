"""PRD 7 PR 7 (#599): the content tables answer only for a document the person can see.

PR 6's walled organisation; Finance's file ("fin") is hidden from the plain reviewer, Sales' file
("sales") and Loose's ("loose") are not. Each gets the same content: a page, a value and its fix,
an invoice and its line, a reading with a cell and a mark, an Ensemble run and reading, a piece,
a tag, the reviewer's word spot and a word suggestion. Every statement the API reads them with is
called directly as the reviewer, so it is proved by itself, not by the route's earlier check:
nothing for the hidden file, the same rows as before for a visible one."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Connection, text

from kaagaz.db import document_stories, ensemble, listing, organization_words, tags, user_words, word_review
from kaagaz.db import review as review_sql
from kaagaz.db.engine import Database
from kaagaz.db.visibility import SEEN_DOCUMENT
from tests.db_support import insert_tag, tag_document
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import Org, _app, _plan, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_SPANS = [
    {"text": "1,200", "raw": "1,200", "region": {"kind": "cell", "page": 1, "row": 1, "column": 1}},
    {
        "text": "Rs",
        "raw": "Rs",
        "origin": "ocr",
        "quality": "unsure",
        "region": {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]},
    },
]
_KEY = "a" * 64


def _content(api: Api, org: Org, document: str) -> dict[str, str]:
    """One of each content row for ``document``; the ids a statement asks for."""
    db, c, plain = api.db, org.c, org.people["plain"].user_id
    first = org.people["admin"].user_id
    api.add_page(c, document)
    _owner(db, "UPDATE pages SET read_quality = 'uncertain' WHERE document_id = :d", {"d": document})
    value = api.add_value(c, document)
    api.add_reader_output(c, document, _SPANS)
    ids = {"value": value}
    with db.customer(c) as conn:
        params = {"c": c, "d": document, "u": first, "v": value, "p": plain}
        conn.execute(
            text(
                "INSERT INTO corrections (customer_id, document_id, value_id, field, old_value, new_value,"
                " choice, corrected_by) VALUES (:c, :d, :v, 'total_amount', '1', '2', 'typed', :u)"
            ),
            params,
        )
        invoice = conn.execute(
            text(
                "INSERT INTO invoices (customer_id, document_id, label, check_status)"
                " VALUES (:c, :d, 'verified', 'passed') RETURNING id"
            ),
            params,
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO invoice_lines (customer_id, invoice_id, document_id, line_no, label)"
                " VALUES (:c, :i, :d, 1, 'verified')"
            ),
            {**params, "i": invoice},
        )
        run = conn.execute(
            text(
                "INSERT INTO ensemble_runs (customer_id, document_id, scope, pages, models, requested_by)"
                " VALUES (:c, :d, 'document', ARRAY[1], ARRAY['m/one'], :u) RETURNING id"
            ),
            params,
        ).scalar_one()
        ids["run"] = str(run)
        ids["reading"] = str(
            conn.execute(
                text(
                    "INSERT INTO ensemble_readings (customer_id, document_id, run_id, word_key, page_no,"
                    " region, before_text, outcome, proposal, reads)"
                    " VALUES (:c, :d, :r, :k, 1, '{}', 'Rs', 'changed', 'Rs.', '[]') RETURNING id"
                ),
                {**params, "r": run, "k": _KEY},
            ).scalar_one()
        )
        ids["piece"] = str(
            conn.execute(
                text(
                    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions,"
                    " label)"
                    " VALUES (:c, :d, 0, 1, 1, 'Rs 1,200', '[{\"page\": 1}]', 'verified') RETURNING id"
                ),
                params,
            ).scalar_one()
        )
        tag = insert_tag(conn, c, first, f"tag {document[:8]}")
        tag_document(conn, c, document, tag)
        conn.execute(
            text(
                "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold,"
                " meaning, meaning_fold, document_id, page_no, status)"
                " VALUES (:c, :u, :w, :w, 'm', 'm', :d, 1, 'pending')"
            ),
            {**params, "w": f"s{document[:8]}"},
        )
    with db.user(c, plain) as conn:  # My words are the person's own (0008)
        params = {"c": c, "d": document, "p": plain, "w": f"w{document[:8]}"}
        word = conn.execute(
            text(
                "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold,"
                " source) VALUES (:c, :p, :w, :w, 'm', 'm', 'manual') RETURNING id"
            ),
            params,
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO user_word_spots (customer_id, user_id, word_id, document_id, page_no, region)"
                ' VALUES (:c, :p, :w, :d, 1, \'{"kind": "box", "page": 1}\')'
            ),
            {**params, "w": word},
        )
    return ids


def _org(api: Api) -> tuple[Org, dict[str, dict[str, str]]]:
    org = _walled(api)
    return org, {name: _content(api, org, org.files[name]) for name in ("fin", "sales")}


# Each statement the API reads a content table with: (name, call) where call(conn, customer, document,
# ids) returns what it read. Every one returns something for a visible document.
_Read = Callable[[Connection, str, str, dict[str, str]], Any]
_READS: list[tuple[str, _Read]] = [
    ("list_pages", lambda conn, c, d, i: listing.list_pages(conn, c, d)),
    ("get_page", lambda conn, c, d, i: listing.get_page(conn, c, d, 1)),
    ("reviewer_fix_count", lambda conn, c, d, i: listing.reviewer_fix_count(conn, c, d)),
    ("invoice_for", lambda conn, c, d, i: listing.invoice_for(conn, c, d)),
    (
        "invoice_lines",
        lambda conn, c, d, i: conn.execute(
            listing._INVOICE_LINES,
            {
                "c": c,
                "d": d,
                "i": conn.execute(text("SELECT id FROM invoices WHERE document_id = :d"), {"d": d}).scalar(),
            },
        ).all(),
    ),
    ("list_values", lambda conn, c, d, i: listing.list_values(conn, c, d)),
    ("sheet_cells", lambda conn, c, d, i: listing.sheet_cells(conn, c, d, 1, 1, 5)),
    ("page_readings", lambda conn, c, d, i: listing.page_readings(conn, c, d, 1)),
    ("page_marks", lambda conn, c, d, i: listing.page_marks(conn, c, d, 1)),
    ("clear_copy_marks", lambda conn, c, d, i: listing.clear_copy(conn, c, d)[1]),
    (
        "word_review pages",
        lambda conn, c, d, i: conn.execute(word_review._PAGES, {"c": c, "d": d}).scalar_one(),
    ),
    (
        "word_review words",
        lambda conn, c, d, i: conn.execute(word_review._WORDS, {"c": c, "d": d}).scalar_one(),
    ),
    (
        "word_review page to keep",
        lambda conn, c, d, i: conn.execute(word_review._PAGE_TO_KEEP, {"c": c, "d": d, "p": 1}).all(),
    ),
    (
        "word_review values by id",
        lambda conn, c, d, i: conn.execute(
            word_review._VALUES_BY_ID, {"c": c, "d": d, "ids": [i["value"]]}
        ).all(),
    ),
    (
        "word_review pieces by id",
        lambda conn, c, d, i: conn.execute(
            word_review._PIECES_BY_ID, {"c": c, "d": d, "ids": [i["piece"]]}
        ).all(),
    ),
    ("latest_run", lambda conn, c, d, i: ensemble.latest_run(conn, c, d)),
    ("run_counts", lambda conn, c, d, i: ensemble.run_counts(conn, c, d, i["run"])["total"]),
    ("latest_readings", lambda conn, c, d, i: ensemble.latest_readings(conn, c, d)),
    ("pending", lambda conn, c, d, i: conn.execute(ensemble._PENDING, {"c": c, "d": d, "r": i["run"]}).all()),
    (
        "reading",
        lambda conn, c, d, i: conn.execute(ensemble._READING, {"c": c, "d": d, "id": i["reading"]}).all(),
    ),
    (
        "word spots",
        lambda conn, c, d, i: conn.execute(
            text(user_words._SPOTS), {"c": c, "d": d, "p": 1, "me": i["me"]}
        ).all(),
    ),
    ("tags", lambda conn, c, d, i: tags.tags_for_documents(conn, c, [d])[d]),
    (
        "word suggestions",
        lambda conn, c, d, i: [
            s for s in organization_words.visible(conn, c, i["me"], reviewer=True) if s.document_id == d
        ],
    ),
]


@pytest.mark.parametrize(("name", "read"), _READS, ids=[name for name, _ in _READS])
def test_every_content_statement_reads_nothing_of_a_hidden_document(
    db: Database, name: str, read: _Read
) -> None:
    api = Api(db)
    org, content = _org(api)
    plain = org.people["plain"].user_id
    with _app(db).user(org.c, plain) as conn:
        hidden = read(conn, org.c, org.files["fin"], {**content["fin"], "me": plain})
        visible = read(conn, org.c, org.files["sales"], {**content["sales"], "me": plain})
    assert visible, name  # the statement does read a visible document's rows
    assert not hidden, (name, hidden)
    member = org.people["member"].user_id  # who may see Finance still does
    with _app(db).user(org.c, member) as conn:
        assert read(conn, org.c, org.files["fin"], {**content["fin"], "me": plain}) or name in (
            "word spots",  # the plain reviewer's own spots
        ), name


def test_a_story_is_never_written_for_a_hidden_document(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"].user_id
    story = document_stories.StoryText("My note", "My note")
    with _app(db).user(org.c, plain) as conn:
        assert document_stories.put_story(conn, org.c, plain, org.files["fin"], story) is None
        assert document_stories.put_story(conn, org.c, plain, org.files["sales"], story) is not None


def test_links_need_both_documents_to_be_visible(db: Database) -> None:
    """Sales and Finance both link to Loose: the plain reviewer sees Loose's link to Sales only,
    none from Finance (hidden), and the member sees both."""
    api = Api(db)
    org = _walled(api)
    f = org.files
    for one in (f["sales"], f["fin"]):
        _owner(
            db,
            "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
            " identifier_value) VALUES (:c, :a, :b, 'invoice_number', 'X'),"
            " (:c, :b, :a, 'invoice_number', 'X')",
            {"c": org.c, "a": one, "b": f["loose"]},
        )
    for name, expected in (("plain", {f["sales"]}), ("member", {f["sales"], f["fin"]})):
        with _app(db).user(org.c, org.people[name].user_id) as conn:
            assert {str(r["id"]) for r in listing.document_links(conn, org.c, f["loose"])} == expected, name
            assert bool(listing.document_links(conn, org.c, f["fin"])) == (name == "member"), name


def test_the_review_queue_holds_no_value_of_a_hidden_file(db: Database) -> None:
    api = Api(db)
    org, content = _org(api)
    plain = org.people["plain"].client
    queue = plain.get("/api/review").json()
    shown = {item["document_id"] for item in queue["items"]}
    assert org.files["sales"] in shown and org.files["fin"] not in shown
    assert queue["total"] == len(queue["items"])
    with _app(db).user(org.c, org.people["member"].user_id) as conn:  # who sees Finance still does
        rows, _ = review_sql.list_queue(conn, org.c, limit=50, offset=0, document_id=None, reasons=None)
    assert org.files["fin"] in {str(row["document_id"]) for row in rows}
    body = {"choice": "first", "remember": False}
    replies = [
        plain.post(f"/api/review/{value}", json=body, headers=CSRF)
        for value in (str(uuid.uuid4()), content["fin"]["value"])
    ]
    assert replies[0].status_code == 404
    assert (replies[1].status_code, replies[1].json()) == (replies[0].status_code, replies[0].json())
    assert _owner(db, "SELECT label FROM extracted_values WHERE id = :v", {"v": content["fin"]["value"]}) == [
        ("needs_review",)
    ]


def test_a_by_id_read_of_a_hidden_document_never_runs_its_content_scan(db: Database) -> None:
    """SEEN_DOCUMENT is worked out once, before the content table is read: for the hidden file the
    pages scan never runs; for a visible one it runs once."""
    api = Api(db)
    org, _ = _org(api)
    statement = listing._PAGE + " WHERE customer_id = :c AND document_id = :d" + SEEN_DOCUMENT
    plain = org.people["plain"].user_id
    for document, loops in ((org.files["fin"], 0), (org.files["sales"], 1)):
        nodes = _nodes(_plan(db, org.c, plain, statement, {"c": org.c, "d": document}))
        scans = [n for n in nodes if n.get("Relation Name") == "pages"]
        assert scans and all(n["Actual Loops"] == loops for n in scans), (document, scans)
        assert any("One-Time Filter" in n for n in nodes)


def _nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [node] + [each for child in node.get("Plans", []) for each in _nodes(child)]
