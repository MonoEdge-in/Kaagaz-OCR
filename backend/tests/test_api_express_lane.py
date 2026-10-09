"""The server picks each job's lane (PRD 5 Q4): a document of 1 to EXPRESS_MAX_PAGES
pages goes to the express lane, anything else to the main one.

* An upload and a new version are counted by the upload service, so the API must
  pass it EXPRESS_MAX_PAGES (without it every upload went to the main lane).
* Read again, of the document or of one page, goes by the page count its last read
  stored; a document never counted goes to the main lane.
"""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_page_fixes import read_again, scanned
from tests.test_api_reprocess import reprocess
from tests.test_api_support import CSRF, PDF_BYTES, Api

pytestmark = pytest.mark.db

CSV = b"invoice,amount\nINV-1,100\n"  # one page


def test_an_upload_of_one_page_goes_express_and_one_never_counted_goes_main(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    one = signed.client.post("/api/documents", files={"file": ("a.csv", CSV, "text/csv")}, headers=CSRF)
    unknown = signed.client.post(
        "/api/documents", files={"file": ("b.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )
    assert (one.status_code, unknown.status_code) == (201, 201)
    assert api.queue.lanes == [True, False]  # PDF_BYTES is no real PDF: not counted


def test_a_new_version_of_one_page_goes_express(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = signed.client.post(
        "/api/documents", files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )
    version = signed.client.post(
        f"/api/documents/{first.json()['document']['id']}/versions",
        files={"file": ("a.csv", CSV, "text/csv")},
        headers=CSRF,
    )
    assert version.status_code == 201
    assert api.queue.lanes == [False, True]


@pytest.mark.parametrize(("pages", "express"), [(1, True), (3, True), (4, False), (None, False)])
def test_read_again_goes_by_the_stored_page_count(db: Database, pages: int | None, express: bool) -> None:
    api = Api(db)  # EXPRESS_MAX_PAGES 3 (settings_factory)
    reviewer = api.new_signed("reviewer")
    doc = api.add_document(reviewer.customer_id, page_count=pages)
    assert reprocess(reviewer, doc).status_code == 200
    assert api.queue.lanes == [express]


@pytest.mark.parametrize(("pages", "express"), [(2, True), (5, False)])
def test_a_page_read_again_goes_by_the_document_page_count(db: Database, pages: int, express: bool) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    doc = scanned(api, reviewer.customer_id, pages=pages)
    assert read_again(reviewer, doc, 1).status_code == 200
    assert api.queue.lanes == [express]
