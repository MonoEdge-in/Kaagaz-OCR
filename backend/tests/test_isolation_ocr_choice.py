"""The scan reader an upload chose (PRD 4 #17) stays with its customer: customer B
posting a version, with a reader chosen, to customer A's document gets 404, and
A's documents and choices are unchanged; B never reads A's choice through the
repository. Through the whole API (session, routes, SQL and row level security).
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.reading import ocr
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db

B_BYTES = b"%PDF-1.7\n" + b"9" * 200


def _post(signed: Signed, url: str, data: bytes) -> tuple[int, dict[str, Any]]:
    reply = signed.client.post(
        url,
        data={"ocr_choice": "rapidocr"},
        files={"file": ("copy.pdf", data, "application/pdf")},
        headers=CSRF,
    )
    return reply.status_code, reply.json()


def _rows(db: Database, customer_id: str) -> list[tuple[object, ...]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(text("SELECT id, family_id, version_no, ocr_choice FROM documents ORDER BY id"))
        return [tuple(row) for row in rows]


def test_b_cannot_add_a_chosen_reader_version_to_as_document(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "_installed_version", lambda package: "3.9.2")
    api = Api(db, settings=make_settings(ocr_engine="rapidocr"))
    a = api.sign_in(api.customer("Sharma Traders")[0])
    b = api.sign_in(api.customer("Mehta Textiles")[0])
    status, body = _post(a, "/api/documents", PDF_BYTES)
    assert status == 201
    a_document = str(body["document"]["id"])
    before = _rows(db, a.customer_id)

    status, body = _post(b, f"/api/documents/{a_document}/versions", B_BYTES)
    assert (status, body["error"]) == (404, {"code": "not_found", "message": "We couldn't find that."})
    assert _rows(db, a.customer_id) == before
    assert [row[3] for row in before] == ["rapidocr"]
    assert _rows(db, b.customer_id) == []
    assert [job.customer_id for job in api.queue.jobs] == [a.customer_id]
    assert PgDocuments(db).get(b.customer_id, a_document) is None
    stored = PgDocuments(db).get(a.customer_id, a_document)
    assert stored is not None and stored.ocr_choice == "rapidocr"
