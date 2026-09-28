"""The document routes against a real database, with fake storage and queue.

Pure helpers (the display name and the status messages) are tested first,
without a database.
"""

from collections.abc import Sequence
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api.routers.documents import display_name
from kaagaz.api.views import status_message
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.masking.identity import IndianIdMasker
from kaagaz.reading.spans import CellLocation, Span
from kaagaz.scanning.gate import CUSTOMER_MESSAGES
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PDF_BYTES, Api, TestClient

# --- pure ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("bill.pdf", "bill.pdf"),
        ("C:\\Users\\ravi\\Desktop\\bill.pdf", "bill.pdf"),
        ("/home/ravi/bill.pdf", "bill.pdf"),
        ("  spaced.pdf  ", "spaced.pdf"),
        ("", "document"),
        (None, "document"),
        ("folder/", "document"),
        ("tab\tand\nnewline.pdf", "tabandnewline.pdf"),
    ],
)
def test_display_name_strips_the_path_and_control_characters(given: str | None, expected: str) -> None:
    assert display_name(given, IndianIdMasker()) == expected


def test_display_name_masks_identity_numbers_and_is_cut_to_255() -> None:
    name = display_name("PAN ABCDE1234F aadhaar 2345 6789 0123.pdf", IndianIdMasker())
    assert "ABCDE1234F" not in name
    assert "2345 6789" not in name
    assert name.endswith("0123.pdf")
    assert len(display_name("a" * 400 + ".pdf", IndianIdMasker())) == 255


def test_status_messages() -> None:
    assert status_message("received", None) == "Waiting for the virus check."
    assert status_message("clean", None) == "Reading the document…"
    assert status_message("ready", None) == "Ready. You can ask about it."
    assert status_message("needs_reader", None) == "Stored safely. This kind of file can't be read yet."
    assert status_message("rejected", "virus_found") == CUSTOMER_MESSAGES["virus_found"]
    assert status_message("rejected", None) == "This file was not processed."
    assert status_message("failed", "corrupt_file") == "This file could not be read. (corrupt_file)"


# --- upload ---------------------------------------------------------------------------


def _upload(client: TestClient, data: bytes, name: str = "bill.pdf") -> httpx.Response:
    reply: httpx.Response = client.post(
        "/api/documents", files={"file": (name, data, "application/pdf")}, headers=CSRF
    )
    return reply


@pytest.mark.db
def test_upload_stores_queues_and_returns_the_summary(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()

    reply = _upload(signed.client, PDF_BYTES, name="C:\\scans\\PAN ABCDE1234F.pdf")

    assert reply.status_code == 201
    body = reply.json()
    assert body["duplicate"] is False
    document = body["document"]
    assert set(document) == {
        "id",
        "display_name",
        "detected_type",
        "size_bytes",
        "status",
        "failure_code",
        "status_message",
        "page_count",
        "created_at",
        "uploaded_by_name",
    }
    assert document["display_name"] == "PAN XXXXXX234F.pdf"
    assert document["detected_type"] == "pdf"
    assert document["size_bytes"] == len(PDF_BYTES)
    assert document["status"] == "received"
    assert document["status_message"] == "Waiting for the virus check."
    assert document["uploaded_by_name"] == "Test member"
    assert api.storage.has(signed.customer_id, document["id"])
    assert [(job.customer_id, job.document_id) for job in api.queue.jobs] == [
        (signed.customer_id, document["id"])
    ]
    assert api.audit_actions(signed.customer_id)[-1] == "document.upload"
    with db.customer(signed.customer_id) as conn:
        stored_names: Sequence[str] = conn.execute(text("SELECT display_name FROM documents")).scalars().all()
    assert "ABCDE1234F" not in " ".join(stored_names)


@pytest.mark.db
def test_same_file_again_is_a_duplicate_with_200(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = _upload(signed.client, PDF_BYTES).json()
    again = _upload(signed.client, PDF_BYTES, name="other name.pdf")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["document"]["id"] == first["document"]["id"]
    assert len(api.queue.jobs) == 1
    assert api.audit_actions(signed.customer_id).count("document.upload") == 1


@pytest.mark.db
@pytest.mark.parametrize(
    ("data", "status", "code"),
    [(b"", 400, "empty_file"), (b"\x00\x01\x02binary", 400, "unsupported_type")],
)
def test_refused_uploads(db: Database, data: bytes, status: int, code: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = _upload(signed.client, data)
    assert reply.status_code == status
    assert reply.json()["error"]["code"] == code
    assert api.queue.jobs == []
    assert api.storage.count() == 0


@pytest.mark.db
def test_too_large_by_content_length_before_reading(db: Database) -> None:
    api = Api(db, settings=make_settings(max_upload_bytes=1024))
    signed = api.new_signed()
    reply = _upload(signed.client, b"%PDF-" + b"0" * (200 * 1024))
    assert reply.status_code == 413
    assert reply.json()["error"]["code"] == "too_large"
    assert api.storage.count() == 0


@pytest.mark.db
def test_too_large_by_the_upload_service(db: Database) -> None:
    api = Api(db, settings=make_settings(max_upload_bytes=1024))
    signed = api.new_signed()
    reply = _upload(signed.client, b"%PDF-" + b"0" * 2000)  # under limit + overhead, over the limit
    assert reply.status_code == 413
    assert reply.json()["error"]["code"] == "too_large"
    assert api.storage.count() == 0


@pytest.mark.db
def test_the_customers_own_limit_applies(db: Database) -> None:
    api = Api(db)  # platform limit 5 MB
    signed = api.new_signed()
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE customers SET max_upload_mb = 1"))
    reply = _upload(signed.client, b"%PDF-" + b"0" * (1024 * 1024 + 10))
    assert reply.status_code == 413


@pytest.mark.db
def test_upload_without_a_file_field_is_400(db: Database) -> None:
    signed = Api(db).new_signed()
    reply = signed.client.post("/api/documents", data={"other": "x"}, headers=CSRF)
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"


@pytest.mark.db
def test_storage_down_is_503_and_nothing_is_recorded(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.storage.fail_put = True
    reply = _upload(signed.client, PDF_BYTES)
    assert reply.status_code == 503
    assert reply.json()["error"]["code"] == "backend_unavailable"
    assert signed.client.get("/api/documents").json()["total"] == 0


@pytest.mark.db
def test_upload_needs_sign_in_and_csrf(db: Database) -> None:
    api = Api(db)
    assert _upload(api.client(), PDF_BYTES).status_code == 401
    signed = api.new_signed()
    reply = signed.client.post("/api/documents", files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "csrf"


# --- list ----------------------------------------------------------------------------


@pytest.mark.db
def test_list_is_newest_first_with_total_and_pagination(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    ids = []
    for n in range(3):
        ids.append(api.add_document(signed.customer_id, name=f"file {n}.pdf"))
        with db.customer(signed.customer_id) as conn:
            conn.execute(
                text("UPDATE documents SET created_at = now() + make_interval(secs => :n) WHERE id = :d"),
                {"n": n, "d": ids[-1]},
            )

    page = signed.client.get("/api/documents?limit=2").json()
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
    rest = signed.client.get("/api/documents?limit=2&offset=2").json()
    assert [item["id"] for item in rest["items"]] == [ids[0]]


@pytest.mark.db
def test_list_search_is_case_insensitive_and_literal(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id, name="Sharma Invoice.pdf")
    api.add_document(signed.customer_id, name="100% paid.pdf")
    api.add_document(signed.customer_id, name="register.xlsx", detected_type="zip")

    def names(query: str) -> list[str]:
        items = signed.client.get("/api/documents", params={"q": query}).json()["items"]
        return sorted(item["display_name"] for item in items)

    assert names("INVOICE") == ["Sharma Invoice.pdf"]
    assert names("%") == ["100% paid.pdf"]
    assert names("_") == []


@pytest.mark.db
def test_list_filters_by_status(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id, status="ready")
    rejected = api.add_document(signed.customer_id, status="rejected", failure_code="virus_found")
    reply = signed.client.get("/api/documents?status=rejected").json()
    assert [item["id"] for item in reply["items"]] == [rejected]
    assert reply["items"][0]["status_message"] == CUSTOMER_MESSAGES["virus_found"]


@pytest.mark.db
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=unknown"])
def test_list_refuses_bad_parameters(db: Database, query: str) -> None:
    signed = Api(db).new_signed()
    reply = signed.client.get(f"/api/documents?{query}")
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"


# --- detail, file, page image, cells ------------------------------------------------------


@pytest.mark.db
def test_detail_has_pages_and_values_in_order(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id)
    api.add_page(signed.customer_id, document_id, 2)
    api.add_page(signed.customer_id, document_id, 1)
    second = api.add_value(signed.customer_id, document_id, page_no=2, field_name="gstin", value="X")
    first = api.add_value(signed.customer_id, document_id, page_no=1, field_name="total_amount", second=None)

    body = signed.client.get(f"/api/documents/{document_id}").json()

    assert body["id"] == document_id
    assert [page["page_no"] for page in body["pages"]] == [1, 2]
    assert body["pages"][0] == {
        "page_no": 1,
        "width": 595.0,
        "height": 842.0,
        "unit": "pt",
        "has_image": True,
    }
    assert [value["id"] for value in body["values"]] == [first, second]
    assert body["values"][0] == {
        "id": first,
        "document_id": document_id,
        "record_key": "document",
        "field": "total_amount",
        "value": "1,200",
        "raw": "1,200",
        "second_reading": None,
        "page_no": 1,
        "region": {"kind": "box", "page": 1, "box": [0.1, 0.2, 0.3, 0.25]},
        "label": "needs_review",
        "label_reason": "readers_disagree",
        "confidence": None,
    }


@pytest.mark.db
def test_unknown_or_malformed_document_id(db: Database) -> None:
    signed = Api(db).new_signed()
    assert signed.client.get("/api/documents/7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e").status_code == 404
    assert signed.client.get("/api/documents/not-a-uuid").status_code == 400


@pytest.mark.db
def test_file_is_served_inline_and_sandboxed(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, status="ready", name="PAN ABCDE1234F.pdf")
    reply = signed.client.get(f"/api/documents/{document_id}/file")
    assert reply.status_code == 200
    assert reply.content == PDF_BYTES
    assert reply.headers["content-type"] == "application/pdf"
    assert reply.headers["content-disposition"] == 'inline; filename="document.pdf"'
    assert reply.headers["content-security-policy"] == "sandbox"
    assert reply.headers["x-content-type-options"] == "nosniff"


@pytest.mark.db
@pytest.mark.parametrize("status", ["rejected", "received", "failed"])
def test_file_is_never_served_unless_it_passed_the_scan(db: Database, status: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, status=status)
    reply = signed.client.get(f"/api/documents/{document_id}/file")
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"


@pytest.mark.db
def test_page_image(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id)
    api.storage.page_images[(signed.customer_id, document_id, 1)] = b"\x89PNG fake"
    reply = signed.client.get(f"/api/documents/{document_id}/pages/1/image")
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "image/png"
    assert reply.content == b"\x89PNG fake"
    assert signed.client.get(f"/api/documents/{document_id}/pages/2/image").status_code == 404


@pytest.mark.db
def test_sheet_cells_come_from_the_first_reader_output(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, detected_type="text", name="register.csv")
    api.add_page(signed.customer_id, document_id, 1, unit="cells", height=3)

    def cell(sheet: int, row: int, column: int, value: str) -> dict[str, Any]:
        return span_to_json(Span(value, value, CellLocation(sheet, row, column)))

    api.add_reader_output(
        signed.customer_id,
        document_id,
        [
            cell(1, 1, 2, "amount"),
            cell(1, 1, 1, "party"),
            cell(1, 2, 1, "Sharma"),
            cell(1, 3, 1, "Mehta"),
            cell(2, 1, 1, "other sheet"),
        ],
    )

    body = signed.client.get(f"/api/documents/{document_id}/pages/1/cells?row_from=1&row_to=2").json()

    assert body == {
        "page_no": 1,
        "total_rows": 3,
        "rows": [
            {"row": 1, "cells": [{"column": 1, "text": "party"}, {"column": 2, "text": "amount"}]},
            {"row": 2, "cells": [{"column": 1, "text": "Sharma"}]},
        ],
    }
    # At most 500 rows per call; a PDF page has no cells.
    assert signed.client.get(f"/api/documents/{document_id}/pages/1/cells?row_from=3&row_to=5000").json()[
        "rows"
    ] == [{"row": 3, "cells": [{"column": 1, "text": "Mehta"}]}]
    assert signed.client.get(f"/api/documents/{document_id}/pages/9/cells").status_code == 404


# --- delete ----------------------------------------------------------------------------


@pytest.mark.db
def test_uploader_can_delete_and_everything_goes(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
    api.add_page(signed.customer_id, document_id)
    api.add_value(signed.customer_id, document_id)
    api.storage.page_images[(signed.customer_id, document_id, 1)] = b"png"

    reply = signed.client.delete(f"/api/documents/{document_id}", headers=CSRF)

    assert reply.status_code == 204
    assert signed.client.get(f"/api/documents/{document_id}").status_code == 404
    assert not api.storage.has(signed.customer_id, document_id)
    assert api.storage.page_images == {}
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM extracted_values")).scalar_one() == 0
    assert api.audit_actions(signed.customer_id)[-1] == "document.delete"


@pytest.mark.db
def test_another_member_cannot_delete_but_an_admin_can(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    document_id = api.add_document(uploader.customer_id, uploaded_by=uploader.user_id)
    colleague = api.sign_in(uploader.customer_id, "member")
    admin = api.sign_in(uploader.customer_id, "admin")

    refused = colleague.client.delete(f"/api/documents/{document_id}", headers=CSRF)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert admin.client.delete(f"/api/documents/{document_id}", headers=CSRF).status_code == 204


@pytest.mark.db
def test_delete_keeps_the_row_when_storage_is_down(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
    document_id = api.add_document(admin.customer_id)
    api.storage.fail_delete = True
    assert admin.client.delete(f"/api/documents/{document_id}", headers=CSRF).status_code == 503
    assert admin.client.get(f"/api/documents/{document_id}").status_code == 200
    assert "document.delete" not in api.audit_actions(admin.customer_id)
