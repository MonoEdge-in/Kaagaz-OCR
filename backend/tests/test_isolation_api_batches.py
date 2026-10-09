"""Batches across users and customers (PRD 5 Q1, Q6, council security): B, signed in, asks
for A's batch with A's exact id and gets the same 404 as for a batch that is not there,
with none of A's names; another user of A's own customer too. "Files ahead" counts only
the uploader's own batch, and B's replies are byte-identical whether A has 0 or 50 files
waiting. The control: A asks and gets the files, so each 404 comes from the filter. The
upload's cap (Q1, 409 batch_full) counts the uploader's own files only: B, or A's
colleague, sending A's exact batch_id while A's drop is full is accepted into a drop of
its own, and never hits or sees A's batch_full."""

import uuid
from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_batches_api import get_batch, in_batch

pytestmark = pytest.mark.db


def test_another_customers_or_users_batch_is_the_same_404_as_an_unknown_one(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    colleague = api.sign_in(a.customer_id)
    batch = str(uuid.uuid4())
    in_batch(api, a, batch, name="sharma-private.pdf")

    own = get_batch(a, batch)
    assert own.status_code == 200  # the control
    assert "sharma-private" in own.text

    unknown = get_batch(b, str(uuid.uuid4()))
    for other in (b, colleague):
        reply = get_batch(other, batch)
        assert (reply.status_code, reply.json()) == (unknown.status_code, unknown.json())
        assert reply.status_code == 404
        assert "sharma" not in reply.text
    assert api.client().get(f"/api/batches/{batch}").status_code == 401


def test_files_ahead_count_only_the_uploaders_own_batch(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    colleague = api.sign_in(a.customer_id)
    batch = str(uuid.uuid4())
    for minute in range(3):  # the colleague's files with the same id, uploaded first
        in_batch(api, colleague, batch, minutes=minute, status="received")
    in_batch(api, a, batch, minutes=1, status="received")
    mine = in_batch(api, a, batch, minutes=5, status="received")

    body = get_batch(a, batch).json()

    assert body["counts"]["total"] == 2
    by_id = {item["id"]: item for item in body["documents"]}
    assert by_id[mine]["status_message"] == "Waiting: 1 file ahead in your batch"
    listed = {item["id"]: item for item in a.client.get("/api/documents").json()["items"]}
    assert listed[mine]["status_message"] == "Waiting: 1 file ahead in your batch"


def test_bs_replies_are_byte_identical_whatever_as_load(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_batch, a_batch = str(uuid.uuid4()), str(uuid.uuid4())
    in_batch(api, b, b_batch, status="ready")
    in_batch(api, b, b_batch, minutes=1, status="received")
    in_batch(api, b, b_batch, minutes=2, status="received")

    def b_replies() -> list[bytes]:
        paths = (f"/api/batches/{b_batch}", "/api/documents")
        return [b.client.get(path).content for path in paths]

    quiet = b_replies()
    for minute in range(50):
        in_batch(api, a, a_batch, minutes=minute, status="received")
    in_batch(api, a, b_batch, status="received")  # even with B's batch id
    busy = b_replies()

    assert busy == quiet
    assert get_batch(a, a_batch).json()["counts"]["waiting"] == 50  # the control: A's load is there


def _upload(signed: Signed, batch: str, data: bytes) -> Any:
    return signed.client.post(
        "/api/documents",
        data={"batch_id": batch},
        files={"file": ("bill.pdf", data, "application/pdf")},
        headers=CSRF,
    )


def test_another_customers_full_batch_is_never_hit_or_seen_by_an_upload(db: Database) -> None:
    api = Api(db, settings=make_settings(max_files_per_batch=2))
    a, b = api.new_signed(), api.new_signed()
    colleague = api.sign_in(a.customer_id)
    batch = str(uuid.uuid4())
    for minute in range(2):
        in_batch(api, a, batch, minutes=minute, name=f"sharma-{minute}.pdf")
    full = _upload(a, batch, PDF_BYTES + b"a's third")
    assert (full.status_code, full.json()["error"]["code"]) == (409, "batch_full")  # the control

    for other, data in ((b, PDF_BYTES + b"b's file"), (colleague, PDF_BYTES + b"colleague's file")):
        reply = _upload(other, batch, data)
        assert reply.status_code == 201, reply.text
        assert "batch_full" not in reply.text
        assert "sharma" not in reply.text
        (own,) = get_batch(other, batch).json()["documents"]  # its own drop, with A's id
        assert own["id"] == reply.json()["document"]["id"]
    assert [item["display_name"] for item in get_batch(a, batch).json()["documents"]] == [
        "sharma-0.pdf",
        "sharma-1.pdf",
    ]
