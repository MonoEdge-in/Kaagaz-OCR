"""``GET /api/batches/{id}`` (PRD 5 Q1, U1) against PostgreSQL: the uploader's own files of
one drop in upload order, each a DocumentSummary, with the drop's counts and pages; a
file in Trash or of another drop is not in it; an upload passes its ``batch_id`` on and
a bad one is 400. Other users and customers: ``test_isolation_api_batches.py``. The cap:
``test_upload_batch_cap.py``."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.views import READER_WAITING
from kaagaz.db.engine import Database
from kaagaz.ingestion.service import UploadService
from tests.test_api_page_fixes import read_page
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db

SUMMARY_KEYS = {
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
    "folder_id",
    "tags",
    "deleted_at",
    "version_no",
    "family_id",
    "can_delete",
    "attempts",
    "max_attempts",
    "newer_version",
    "progress",
    "time_left",
    "doc_type",
    "languages",
    "pages_unreadable",
    "pages_uncertain",
    "values_to_review",
    "words_unsure",
}
BATCH_KEYS = {"id", "documents", "counts", "pages_read", "pages_total", "time_left"}


def in_batch(api: Api, signed: Signed, batch: str, *, minutes: int = 0, **columns: Any) -> str:
    """One of ``signed``'s files in ``batch``, uploaded ``minutes`` after the clock, with
    ``progress`` (a dict) and any other documents column."""
    progress = columns.pop("progress", None)
    document_id = api.add_document(
        signed.customer_id,
        uploaded_by=signed.user_id,
        created_at=api.clock() + timedelta(minutes=minutes),
        **columns,
    )
    with api.db.customer(signed.customer_id) as conn:
        conn.execute(
            text(
                "UPDATE documents SET batch_id = :b, progress = CAST(:p AS jsonb)"
                " WHERE customer_id = :c AND id = :d"
            ),
            {
                "b": batch,
                "p": None if progress is None else json.dumps(progress),
                "c": signed.customer_id,
                "d": document_id,
            },
        )
    return document_id


def get_batch(signed: Signed, batch: str) -> Any:
    return signed.client.get(f"/api/batches/{batch}")


def test_a_batch_shows_its_files_in_upload_order_with_counts_and_pages(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    batch, other = str(uuid.uuid4()), str(uuid.uuid4())
    ready = in_batch(api, signed, batch, page_count=2)
    flagged = in_batch(api, signed, batch, minutes=1, page_count=1)
    read_page(
        api, signed.customer_id, flagged, read_quality="uncertain", read_issues=["faint"], unsure_words=14
    )
    reading = in_batch(
        api,
        signed,
        batch,
        minutes=2,
        status="clean",
        page_count=4,
        progress={"step": "reading", "done": 1, "total": 4},
    )
    waiting = in_batch(api, signed, batch, minutes=3, status="received", page_count=3)
    stalled = in_batch(
        api, signed, batch, minutes=4, status="clean", page_count=1, progress={"step": "waiting"}
    )
    in_batch(api, signed, batch, minutes=5, deleted_at=api.clock(), deleted_by=signed.user_id)  # in Trash
    in_batch(api, signed, other)  # another drop
    api.add_document(signed.customer_id, uploaded_by=signed.user_id)  # in no drop

    reply = get_batch(signed, batch)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == BATCH_KEYS
    assert body["id"] == batch
    assert [item["id"] for item in body["documents"]] == [ready, flagged, reading, waiting, stalled]
    assert all(set(item) == SUMMARY_KEYS for item in body["documents"])
    assert body["counts"] == {"total": 5, "ready": 1, "reading": 1, "waiting": 2, "attention": 1, "failed": 0}
    assert (body["pages_read"], body["pages_total"]) == (2 + 1 + 1, 2 + 1 + 4 + 3 + 1)
    by_id = {item["id"]: item for item in body["documents"]}
    assert by_id[flagged]["words_unsure"] == 14
    assert by_id[waiting]["status_message"] == "Waiting: 1 file ahead in your batch"  # the one being read
    assert (by_id[stalled]["status_message"], by_id[stalled]["progress"]) == (READER_WAITING, None)


def test_an_unknown_batch_is_404_and_a_bad_id_is_400(db: Database) -> None:
    signed = Api(db).new_signed()
    reply = get_batch(signed, str(uuid.uuid4()))
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert get_batch(signed, "not-a-batch").status_code == 400


def _upload(signed: Signed, **form: str) -> Any:
    return signed.client.post(
        "/api/documents", data=form, files={"file": ("bill.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )


def test_an_upload_passes_its_batch_on_and_a_bad_batch_id_is_refused_before_storing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    signed = api.new_signed()
    given: list[str | None] = []
    accept = UploadService.accept

    def spy(self: UploadService, *args: Any, **kwargs: Any) -> Any:
        given.append(kwargs.get("batch_id"))
        return accept(self, *args, **kwargs)

    monkeypatch.setattr(UploadService, "accept", spy)
    batch = str(uuid.uuid4())

    assert _upload(signed, batch_id=batch.upper()).status_code == 201
    for bad in ("not-an-id", "1234", ""):
        reply = _upload(signed, batch_id=bad)
        assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request"), bad
    assert given == [batch]  # normalised; nothing reached the service for a bad id
    assert len(api.queue.jobs) == 1
