"""Customer separation for the regions of a page read again (#385, migration 0056): one
customer can neither mark regions on another customer's page, through the store or the API,
nor see the regions marked on it."""

import pytest

from kaagaz.db.documents import PgDocuments, request_page_read_again
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.test_api_page_fixes import scanned
from tests.test_api_support import CSRF, Api
from tests.test_documents_page_marks_db import T0, _read

pytestmark = pytest.mark.db

REGION = (0.2, 0.31, 0.5, 0.32)


def test_another_customer_neither_marks_nor_sees_the_regions(db: Database) -> None:
    customer, _, doc = _read(db)
    other, _ = new_customer(db)
    with db.customer(other) as conn:
        assert not request_page_read_again(conn, other, doc, 1, now=T0, regions=[REGION])
    assert PgDocuments(db).regions_to_read_again(customer, doc) == {}

    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 1, now=T0, regions=[REGION])
    assert PgDocuments(db).regions_to_read_again(other, doc) == {}
    assert PgDocuments(db).regions_to_read_again(customer, doc) == {1: (REGION,)}


def test_the_api_finds_no_page_of_another_customer(db: Database) -> None:
    api = Api(db)
    owner = api.new_signed("reviewer")
    stranger = api.new_signed("reviewer")
    doc = scanned(api, owner.customer_id)

    reply = stranger.client.post(
        f"/api/documents/{doc}/pages/1/read-again", json={"regions": [list(REGION)]}, headers=CSRF
    )

    assert reply.status_code == 404
    assert PgDocuments(db).regions_to_read_again(owner.customer_id, doc) == {}
    assert api.queue.jobs == []
