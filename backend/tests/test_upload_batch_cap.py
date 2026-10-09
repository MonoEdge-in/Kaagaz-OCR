"""A drop holds at most MAX_FILES_PER_BATCH files (PRD 5 Q1, council security): the next
upload into it is 409 ``batch_full`` with the PRD's words, refused before anything is
stored or queued; the cap counts the uploader's own files out of Trash, so a colleague's
files with the same id and a file in Trash leave room."""

import uuid
from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_batches_api import in_batch

pytestmark = pytest.mark.db


def _upload(signed: Signed, batch: str, data: bytes = PDF_BYTES) -> Any:
    return signed.client.post(
        "/api/documents",
        data={"batch_id": batch},
        files={"file": ("bill.pdf", data, "application/pdf")},
        headers=CSRF,
    )


def test_the_file_past_the_cap_is_409_batch_full_and_nothing_is_stored(db: Database) -> None:
    api = Api(db, settings=make_settings(max_files_per_batch=3))
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)
    batch = str(uuid.uuid4())
    for minute in range(2):
        in_batch(api, signed, batch, minutes=minute)
    in_batch(api, signed, batch, deleted_at=api.clock(), deleted_by=signed.user_id)  # in Trash: not counted
    in_batch(api, colleague, batch)  # another user's: not counted
    assert _upload(signed, batch).status_code == 201  # 2 of 3 counted: room for one
    in_batch(api, signed, batch, minutes=2)  # the drop is full whether or not the upload joined it
    stored = api.storage.count()

    full = _upload(signed, batch, PDF_BYTES + b"another file")

    assert full.status_code == 409
    assert full.json()["error"] == {
        "code": "batch_full",
        "message": "Up to 3 files at a time. Drop the rest after these start.",
    }
    assert api.storage.count() == stored  # refused before storing
    assert len(api.queue.jobs) == 1  # only the first upload was queued
    assert _upload(signed, str(uuid.uuid4()), PDF_BYTES + b"another file").status_code == 201  # a new drop
