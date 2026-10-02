"""Customer A's chat scope can never reach customer B's documents (PRD 3 H2, edge case 9).

The whole API with the real AnswerService (no model) on PostgreSQL. B's folder,
tag or document ids in A's ``POST /api/chat`` scope match nothing, so A gets "not
found" (never B's answer, never A's whole library); a saved scope replayed after
its folder was deleted matches nothing too. Each is paired with a positive control.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_answering_fixed_queries_db import add_values, row
from tests.test_api_support import CSRF, Api, Signed
from tests.test_search_db import add_pieces

pytestmark = pytest.mark.db

QUESTIONS = ("What is the grand total of INV-1042?", "What was the total profit last month?")
NOW = datetime(2026, 9, 27, 6, 30, tzinfo=UTC)


def _library(api: Api, signed: Signed) -> dict[str, str]:
    """A folder and a tag holding one document with an invoice piece and an August profit."""
    customer, user = signed.customer_id, signed.user_id
    folder = api.add_folder(customer, user, "Suppliers")
    document = api.add_document(customer, folder_id=folder)
    tag = api.add_tag(customer, user, "gst")
    api.tag_document(customer, document, tag)
    add_pieces(api.db, customer, document, [("Invoice INV-1042\nGrand Total: Rs. 1,20,000", None)])
    add_values(api.db, customer, document, row(2, "Sharma Traders", 120000, date(2026, 8, 10)))
    return {"folder": folder, "tag": tag, "document": document}


def _ask(signed: Signed, question: str, **body: Any) -> dict[str, Any]:
    reply = signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    return dict(reply.json())


def _world(db: Database) -> tuple[Signed, dict[str, str], dict[str, str]]:
    api = Api(db, answers=AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW))
    a, b = api.new_signed(), api.new_signed()
    return a, _library(api, a), _library(api, b)


def test_bs_folder_tag_or_document_ids_in_as_scope_find_nothing(db: Database) -> None:
    a, mine, theirs = _world(db)
    for question in QUESTIONS:
        for field, key in (("folder_id", "folder"), ("tag_ids", "tag"), ("document_ids", "document")):
            for library in (mine, theirs):  # A's own ids are the positive control
                ids: Any = library[key] if key == "folder" else [library[key]]
                answer = _ask(a, question, scope={field: ids})["answer"]["answer"]
                seen = {c["document_id"] for c in answer["citations"]} | {
                    d["id"] for d in answer["closest_documents"]
                }
                expected = ("answered", {mine["document"]}) if library is mine else ("not_found", set())
                assert (answer["status"], seen) == expected, (question, field)


def test_a_saved_scope_replayed_after_its_folder_is_deleted_finds_nothing(db: Database) -> None:
    a, mine, _ = _world(db)
    first = _ask(a, QUESTIONS[0], scope={"folder_id": mine["folder"]})
    assert first["answer"]["answer"]["status"] == "answered"
    with db.customer(a.customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET folder_id = NULL WHERE customer_id = :c"), {"c": a.customer_id}
        )
        conn.execute(text("DELETE FROM folders WHERE customer_id = :c"), {"c": a.customer_id})

    replayed = _ask(a, QUESTIONS[0], conversation_id=first["conversation_id"])
    widened = _ask(a, QUESTIONS[0], conversation_id=first["conversation_id"], scope={})

    assert replayed["answer"]["answer"]["status"] == "not_found"
    assert replayed["question"]["scope"]["folder_id"] == mine["folder"]
    assert widened["answer"]["answer"]["status"] == "answered"
