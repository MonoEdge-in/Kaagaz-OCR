"""Read again for regions of a page (#385, migration 0057), against a real PostgreSQL: the
store (``request_page_read_again`` with regions, ``PgDocuments.regions_to_read_again``, the
marks cleared with a failure for good) and the API (POST
/api/documents/{id}/pages/{n}/read-again with ``regions``: stored, counted in the audit row,
and a bad box refused with nothing changed). Customer separation is
tests/test_isolation_read_again_regions_db.py."""

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, request_page_read_again
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from tests.test_api_page_fixes import marks, scanned
from tests.test_api_support import CSRF, NOW, Api
from tests.test_documents_page_marks_db import T0, T1, _read

pytestmark = pytest.mark.db

TOTAL = (0.2, 0.31, 0.5, 0.32)
NAME = (0.1, 0.05, 0.4, 0.08)


def _regions(api: Api, customer_id: str, document_id: str) -> dict[int, object]:
    with api.db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT page_no, read_again_regions FROM pages WHERE document_id = :d ORDER BY page_no"),
            {"d": document_id},
        ).all()
    return {int(row[0]): row[1] for row in rows}


def test_the_regions_are_kept_with_the_mark_and_the_worker_finds_them(db: Database) -> None:
    customer, _, doc = _read(db)
    documents = PgDocuments(db)
    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 2, now=T0, regions=[TOTAL, NAME])
        assert request_page_read_again(conn, customer, doc, 1, now=T0)  # the whole page
    assert documents.pages_to_read_again(customer, doc) == (1, 2)
    assert documents.regions_to_read_again(customer, doc) == {2: (TOTAL, NAME)}

    with db.customer(customer) as conn:  # a later whole-page request replaces the regions
        assert request_page_read_again(conn, customer, doc, 2, now=T1)
    assert documents.regions_to_read_again(customer, doc) == {}


def test_a_failure_for_good_clears_the_regions_with_the_marks(db: Database) -> None:
    customer, _, doc = _read(db)
    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 1, now=T0, regions=[TOTAL])
    run = PgRuns(db).start(customer, doc, now=T1)
    PgDocuments(db).set_failed(customer, doc, "unreadable", run_id=run)
    assert PgDocuments(db).regions_to_read_again(customer, doc) == {}
    with db.customer(customer) as conn:
        left = conn.execute(
            text("SELECT count(*) FROM pages WHERE document_id = :d AND read_again_regions IS NOT NULL"),
            {"d": doc},
        )
        assert left.scalar_one() == 0


def test_the_api_reads_only_the_regions_asked_for(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = scanned(api, c)

    reply = reviewer.client.post(
        f"/api/documents/{doc}/pages/2/read-again", json={"regions": [list(TOTAL)]}, headers=CSRF
    )

    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "received"
    assert marks(api, c, doc)[2] == (NOW, None, None)
    assert _regions(api, c, doc) == {1: None, 2: [list(TOTAL)]}
    assert api.audit_rows(c)[-1] == (
        "document.reprocess",
        "document",
        doc,
        {"status": "ready", "page_no": 2, "regions": 1},
    )
    assert len(api.queue.jobs) == 1


@pytest.mark.parametrize(
    "regions",
    [
        [],  # none
        [[0.1, 0.2, 0.3]],  # three numbers
        [[0.5, 0.2, 0.4, 0.3]],  # x1 before x0
        [[0.1, 0.2, 0.3, 1.5]],  # off the page
        [[0.1, 0.2, 0.1, 0.4]],  # no area
        [list(TOTAL)] * 11,  # more than MAX_READ_AGAIN_REGIONS
    ],
)
def test_a_bad_region_is_refused_and_nothing_changes(db: Database, regions: list[list[float]]) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    doc = scanned(api, c)

    reply = reviewer.client.post(
        f"/api/documents/{doc}/pages/1/read-again", json={"regions": regions}, headers=CSRF
    )

    assert reply.status_code == 400, reply.text
    assert marks(api, c, doc) == {1: (None, None, None), 2: (None, None, None)}
    assert _regions(api, c, doc) == {1: None, 2: None}
    assert api.queue.jobs == []
