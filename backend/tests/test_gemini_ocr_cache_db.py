"""The Gemini reader with the real page cache, ``PgPageReads`` (PRD 5 G7, F5), against PostgreSQL.

A page the model read is in ``page_reads`` at once, so a retry of the same document
(after a crash, an outage or a lost lease) takes it from there with no model call;
a page read again replaces its row. The model client is the fake from
``tests.test_gemini_ocr``: never the real API.
"""

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey, PgPageReads
from kaagaz.reading.gemini_ocr import GEMINI_OCR_PROMPT_VERSION, pixels_sha256
from kaagaz.reading.ocr import DocumentPages
from tests.db_support import count_rows, insert_document, new_customer
from tests.test_gemini_ocr import MODEL, Built, FakeClient, FakeFinder, line, page, reply, value

pytestmark = pytest.mark.db

_ROWS = "SELECT count(*) FROM page_reads WHERE document_id = :d"


def test_g7_a_page_read_once_is_taken_from_page_reads_with_no_second_call(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    pages = DocumentPages(customer_id=customer, document_id=document, cache=PgPageReads(db))
    image = page()
    content = value(line("सर्वे नंबर [?]", (105, 110, 125, 510), unsure=("नंबर",)))

    first = Built(FakeClient(reply(content, 1700, 4000)), FakeFinder((100.0, 200.0, 500.0, 240.0)))
    with first.engine.document(pages):
        fresh = first.engine.read_page(image, page_no=1)
    retry = Built(FakeClient(), FakeFinder((100.0, 200.0, 500.0, 240.0)))  # the job again, new worker
    with retry.engine.document(pages):
        cached = retry.engine.read_page(image, page_no=1)

    assert retry.client.calls == []
    assert cached.cached and not fresh.cached
    assert cached.lines == fresh.lines
    key = PageKey(customer, document, 1, pixels_sha256(image), MODEL, GEMINI_OCR_PROMPT_VERSION)
    assert PgPageReads(db).get(key) == (content, 1700, 4000)


def test_f5_a_page_read_again_replaces_its_row_in_page_reads(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    cache = PgPageReads(db)
    image = page()
    first = Built(FakeClient(reply(value(line("first")), 1, 2)))
    with first.engine.document(DocumentPages(customer_id=customer, document_id=document, cache=cache)):
        first.engine.read_page(image, page_no=1)
    again = Built(FakeClient(reply(value(line("harder")), 3, 4)))
    asked = DocumentPages(customer_id=customer, document_id=document, cache=cache, read_again=frozenset({1}))
    with again.engine.document(asked):
        read = again.engine.read_page(image, page_no=1)

    assert [item.text for item in read.lines] == ["harder"]
    assert again.client.calls[0].media_resolution == again.settings.ocr_retry_media_resolution
    assert again.client.calls[0].thinking_level == again.settings.ocr_retry_thinking_level
    with db.customer(customer) as conn:
        assert count_rows(conn, _ROWS, {"d": document}) == 1
    key = PageKey(customer, document, 1, pixels_sha256(image), MODEL, GEMINI_OCR_PROMPT_VERSION)
    assert cache.get(key) == (value(line("harder")), 3, 4)
