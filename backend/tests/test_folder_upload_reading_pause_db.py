"""A folder upload meets the reading pause (#484 with #478) against PostgreSQL: its files are
stored and queued as any upload's; the worker's admit lets in what the day's page limit has room
for and holds the rest; the upload's progress counts the held files apart from the ones reading,
with the pause's own words; and the release sweep queues only as many as the room left."""

from contextlib import ExitStack
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.views import PAUSED_FOR_TODAY
from kaagaz.db import reading_days
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.reading_pause import DAILY_LIMIT, ReadingPaused, release_held
from tests.test_api_folder_upload_files import planned, send
from tests.test_api_support import PDF_BYTES, Api, Signed
from tests.test_reading_pause_db import NIGHT, gate, held, mine, set_limit

pytestmark = pytest.mark.db

PROGRESS = ("stored", "reading", "ready", "held", "held_message")


def _progress(signed: Signed, upload: str) -> tuple[Any, ...]:
    summary = signed.client.get(f"/api/folder-uploads/{upload}").json()
    return tuple(summary[name] for name in PROGRESS)


def test_a_folder_upload_into_a_paused_organisation_is_held_said_so_and_released_by_the_room_left(
    db: Database,
) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    set_limit(db, customer, 2)
    files = {f"A/B/bill {n}.pdf": PDF_BYTES + f"bill {n}".encode() for n in range(5)}
    body, items = planned(signed, files)
    documents = []
    for one, data in zip(items, files.values(), strict=True):
        reply = send(signed, one["id"], data)
        assert reply.status_code == 201, reply.text
        documents.append(reply.json()["document"]["id"])
    assert [job.document_id for job in api.queue.jobs] == documents  # queued as any upload's

    # The worker takes them in order: two are let in (one page each), the other three held.
    pause = gate(db, NIGHT)
    with ExitStack() as reading:
        for document in documents[:2]:
            reading.enter_context(pause.admit(customer, document))
        for document in documents[2:]:
            with pytest.raises(ReadingPaused), pause.admit(customer, document):
                pass
        with db.customer(customer) as conn:  # the two let in are read, a page each
            reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=2, cost_usd=0.0)
            conn.execute(
                text("UPDATE documents SET status = 'ready' WHERE id = ANY(CAST(:d AS uuid[]))"),
                {"d": documents[:2]},
            )
    assert [held(db, customer, document) for document in documents] == [None, None, *[DAILY_LIMIT] * 3]
    assert _progress(signed, body["id"]) == (5, 0, 2, 3, PAUSED_FOR_TODAY)
    (listed,) = signed.client.get("/api/folder-uploads").json()["items"]
    assert (listed["held"], listed["held_message"]) == (None, None)  # one upload's progress only

    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []  # no room left today
    set_limit(db, customer, 4)  # raised: room for two more today
    released = mine(release_held(db, sent.append, now=NIGHT), customer)
    assert released == sorted(documents[2:4])  # oldest first, only as many as the room left
    assert _progress(signed, body["id"]) == (5, 2, 2, 1, PAUSED_FOR_TODAY)

    morning = NIGHT + timedelta(minutes=35)  # a new India day: the last one goes too
    assert mine(release_held(db, sent.append, now=morning), customer) == [documents[4]]
    assert _progress(signed, body["id"]) == (5, 3, 2, 0, None)
