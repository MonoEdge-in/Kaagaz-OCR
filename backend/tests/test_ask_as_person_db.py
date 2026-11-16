"""PRD 7 phase 1, PR 2 (#585) on a real PostgreSQL: the transactions an API request opens beyond
kaagaz/api run as the signed-in person (``Database.user``), and a worker's stay on the
organisation (``Database.customer``) until workers get their own role (PR 3).

``watch`` notes every transaction the test's ``db`` opens: which kind, with which ids, from which
function and on which thread. Pinned: an Ask's every transaction, on its early search's thread and
its fused search's threads; a streamed Ask through the API, where only ``load_user`` (whom the
session names, PR 1's ALLOWED) opens one without the person; an upload and its duplicate
(PgDocuments built for the request), while PgDocuments with no person (a worker's) stays on the
organisation; and the usage ledger, saved as the person for a request and on the organisation for
a worker's work, even one a user asked for. The static guard is test_ask_as_person_guard.py.
"""

import sys
import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.usage import record_call, record_page, usage_scope
from kaagaz.answering.service import AnswerService
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.openrouter_fake import json_stream, running_fake
from tests.settings_factory import make_settings
from tests.test_answering_golden import NOW
from tests.test_api_chat_stream import DRAFT, _embeddings, _stopped, names, world
from tests.test_api_storage_limit import upload
from tests.test_api_support import PDF_BYTES, Api
from tests.test_ask_speed_db import PIECES, AspectModel
from tests.test_search_db import add_document, add_pieces

pytestmark = pytest.mark.db

Opened = list[tuple[str, tuple[str, ...], str, int]]  # (kind, ids, opened by, thread)


def watch(db: Database, monkeypatch: pytest.MonkeyPatch, *more: Database) -> Opened:
    """Every ``customer``, ``user`` and ``system`` transaction ``db`` (and ``more``) opens from now on:
    the function that asked for it (through ``user_or_customer``: its caller) and its thread.
    Changed on purpose (#595): the app under test has its own database, in the API role."""
    opened: Opened = []

    def watched(kind: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def each(*ids: str) -> Any:
            frame = sys._getframe(1)
            if frame.f_code.co_name == "user_or_customer" and frame.f_back is not None:
                frame = frame.f_back
            opened.append((kind, ids, frame.f_code.co_name, threading.get_ident()))
            return real(*ids)

        return each

    for database in (db, *more):
        for kind in ("customer", "user", "system"):
            monkeypatch.setattr(database, kind, watched(kind, getattr(database, kind)))
    return opened


def test_every_transaction_of_an_ask_is_the_askers_on_every_thread(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, person = new_customer(db)
    add_pieces(db, customer, add_document(db, customer, name="offer-letter.pdf"), list(PIECES))
    settings = make_settings(model_embed="test-embed", answer_whole_doc_tokens=0)
    model = AspectModel(["holidays", "casual leave", "maternity leave"])  # three fused searches at once
    answers = AnswerService(db=db, model=model, settings=settings, clock=lambda: NOW)
    opened = watch(db, monkeypatch)

    answered = answers.answer(customer, "How many leaves do I get?", user_id=person)  # early search too

    assert answered.status == "answered"
    assert opened and {(kind, ids) for kind, ids, _, _ in opened} == {("user", (customer, person))}
    threads = {thread for _, _, _, thread in opened}
    assert threading.get_ident() in threads and len(threads) > 2  # the early search's, the fused ones


@pytest.fixture
def openrouter() -> Any:
    with running_fake() as server:
        server.responder = _embeddings
        yield server


def _no_person_but_load_user(opened: Opened) -> list[tuple[str, tuple[str, ...], str]]:
    return [(kind, ids, by) for kind, ids, by, _ in opened if kind != "user" and by != "load_user"]


def test_a_streamed_ask_runs_as_the_asker_beyond_the_api(
    db: Database, openrouter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world(db, openrouter)
    openrouter.script("completions", json_stream(DRAFT, size=9))
    opened = watch(db, monkeypatch, w.api.app_db)

    found = w.stream()
    _stopped()

    assert names(found)[-1] == "done"
    assert _no_person_but_load_user(opened) == []
    asker = (w.signed.customer_id, w.signed.user_id)
    assert {ids for kind, ids, _, _ in opened if kind == "user"} == {asker}
    by = {name for kind, _, name, _ in opened if kind == "user"}
    assert {"search", "cards", "usage_scope"} <= by  # the Ask service's own, and the ledger's
    assert len({thread for _, _, _, thread in opened}) > 1  # the stream pool's thread too


def test_an_upload_runs_as_the_uploader_and_a_workers_documents_stay_on_the_organisation(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    signed = api.new_signed()
    opened = watch(db, monkeypatch, api.app_db)

    first = upload(signed, PDF_BYTES)
    again = upload(signed, PDF_BYTES)  # the duplicate check finds the first

    assert first.status_code == 201, first.text
    assert again.json()["duplicate"] is True
    assert _no_person_but_load_user(opened) == []
    uploader = (signed.customer_id, signed.user_id)
    assert {ids for kind, ids, _, _ in opened if kind == "user"} == {uploader}
    by = {name for kind, _, name, _ in opened if kind == "user"}
    assert {"find_by_fingerprint", "add_if_absent", "storage_room"} <= by  # PgDocuments, as the person

    opened.clear()
    document = first.json()["document"]["id"]
    assert PgDocuments(db).get(signed.customer_id, document) is not None  # a worker's: no person
    assert PgDocuments(db, user_id=signed.user_id).get(signed.customer_id, document) is not None
    assert [(kind, ids) for kind, ids, _, _ in opened] == [
        ("customer", (signed.customer_id,)),
        ("user", uploader),
    ]


def test_the_ledger_is_saved_as_the_person_for_a_request_and_on_the_organisation_for_a_worker(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, person = new_customer(db)
    opened = watch(db, monkeypatch)

    with usage_scope(db, customer_id=customer, user_id=person, kind="chat", as_person=True):
        record_call("google/m", None, 10, 5, 0, 0.001)
        record_page("google/m", thinking="low", resolution="high")  # the live counter's write too
    # A worker's work for a user (an Ensemble run's requester): billed to them, on the organisation.
    with usage_scope(db, customer_id=customer, user_id=person, kind="ensemble_scan"):
        record_call("google/m", None, 10, 5, 0, 0.001, thinking="low", resolution="high")
        record_page("google/m", thinking="low", resolution="high")

    assert [(kind, ids, by) for kind, ids, by, _ in opened] == [
        ("user", (customer, person), "count_live"),
        ("user", (customer, person), "usage_scope"),
        ("customer", (customer,), "count_live"),
        ("customer", (customer,), "usage_scope"),
    ]
    monkeypatch.undo()
    with db.customer(customer) as conn:
        saved = conn.execute(
            text("SELECT kind, user_id::text FROM model_usage WHERE customer_id = :c ORDER BY kind"),
            {"c": customer},
        ).all()
    assert [tuple(row) for row in saved] == [("chat", person), ("ensemble_scan", person)]
