"""The operator's insights routes against PostgreSQL (council 11 Oct 2026, F1-F4; 0024):
with people, a file, a chat, feedback, a run, spend and audit rows planted in two
organisations, no reply holds a person, a file, a text or an id other than an
organisation's; a key that speaks of users or documents carries a number only; reads write
no ``platform_audit`` row; a disabled operator gets 401. The numbers themselves:
``test_operator_insights_db.py``."""

import json
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import Api
from tests.test_operator_auth_db import _DISABLED
from tests.test_operator_insights import BOARD, INSIGHTS, QUALITY
from tests.test_operator_orgs_db import _platform, _signed_in

pytestmark = pytest.mark.db

MODEL = "google/gemini-3-flash-preview"
_CONVERSATION = (
    "INSERT INTO conversations (customer_id, user_id, title, created_at)"
    " VALUES (:c, :u, :t, :at) RETURNING id"
)
_MESSAGE = (
    "INSERT INTO messages (customer_id, conversation_id, role, text, answer, created_at)"
    " VALUES (:c, :v, :role, :text, CAST(:answer AS jsonb), :at) RETURNING id"
)
_FEEDBACK = (
    "INSERT INTO feedback (customer_id, message_id, user_id, rating, reason, created_at)"
    " VALUES (:c, :m, :u, 'down', :reason, :at)"
)
_RUN = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status, config,"
    " pages_ocr, pages_gemini) VALUES (:c, :d, :at, :at, 'ready', CAST(:config AS jsonb), 1, 1)"
)
_USAGE = (
    "INSERT INTO model_usage (customer_id, user_id, document_id, conversation_id, kind, model, calls,"
    " input_tokens, output_tokens, reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:c, :u, :d, :v, 'scan', :model, 1, 1000, 200, 0, 0.01, false, :at)"
)
_AUDIT = (
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details, created_at)"
    " VALUES (:c, :u, :a, 'document', :d, CAST(:details AS jsonb), :at)"
)
_ACTIONS = ("document.upload", "auth.login", "chat.ask", "review.resolve")
# Words that may never be in a reply key, and the ones a key may hold only for a number.
_NEVER_IN_KEYS = ("email", "name", "conversation", "message", "reason", "details", "quote")
_COUNTS_ONLY = ("user", "document")


def _plant(api: Api, label: str) -> tuple[str, list[str]]:
    """An organisation with one of each kind of row the insights read, today on the app's
    clock: (its id, every value no reply may hold)."""
    customer, _ = api.customer(f"Org {label} insights")
    member, email = api.add_user(customer, "member")
    at = api.clock()
    document = api.add_document(customer, name="secret-ledger.pdf", uploaded_by=member, created_at=at)
    secret = {
        "person": f"Sentinel Person {label}",
        "title": f"sentinel-title-{label}",
        "question": f"sentinel-question-{label}",
        "answer": f"sentinel-answer-{label}",
        "quote": f"sentinel-quote-{label}",
        "reason": f"sentinel-reason-{label}",
        "details": f"sentinel-details-{label}",
    }
    answer = {
        "status": "answered",
        "label": "verified",
        "route": "search",
        "citations": [{"document_id": document, "page": 1, "quote": secret["quote"]}],
    }
    with api.db.user(customer, member) as conn:

        def one(statement: str, **params: Any) -> str:
            return str(conn.execute(text(statement), {"c": customer, "at": at, **params}).scalar_one())

        conn.execute(
            text("UPDATE users SET display_name = :n WHERE customer_id = :c AND id = :u"),
            {"n": secret["person"], "c": customer, "u": member},
        )
        conversation = one(_CONVERSATION, u=member, t=secret["title"])
        question = one(_MESSAGE, v=conversation, role="user", text=secret["question"], answer=None)
        answered = one(
            _MESSAGE, v=conversation, role="assistant", text=secret["answer"], answer=json.dumps(answer)
        )
        params = {"c": customer, "u": member, "d": document, "v": conversation, "m": answered, "at": at}
        conn.execute(text(_FEEDBACK), {**params, "reason": secret["reason"]})
        config = {"read_model": MODEL, "seconds_reading": 2.0}
        conn.execute(text(_RUN), {**params, "config": json.dumps(config)})
        conn.execute(text(_USAGE), {**params, "model": MODEL})
        for action in _ACTIONS:
            details = {"choice": "second", "note": secret["details"], "name": "secret-ledger.pdf"}
            conn.execute(text(_AUDIT), {**params, "a": action, "details": json.dumps(details)})
    return customer, [
        member,
        email,
        document,
        conversation,
        question,
        answered,
        "secret-ledger",
        *secret.values(),
    ]


def _keys(value: Any) -> list[tuple[str, Any]]:
    """Every (key, value) in a reply, at any depth."""
    if isinstance(value, dict):
        return [pair for key, item in value.items() for pair in [(key, item), *_keys(item)]]
    if isinstance(value, list):
        return [pair for item in value for pair in _keys(item)]
    return []


def test_no_reply_holds_a_person_a_file_a_text_or_any_id_but_an_organisations(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, hidden_a = _plant(api, "A")
    b, hidden_b = _plant(api, "B")
    audited = _platform(operator)
    replies = {path: client.get(path, params={"days": 7}) for path in (INSIGHTS, QUALITY, BOARD)}
    for path, reply in replies.items():
        assert reply.status_code == 200, (path, reply.text)
        for hidden in (*hidden_a, *hidden_b, operator.email):
            assert hidden not in reply.text, (path, hidden)
        for key, value in _keys(reply.json()):
            assert not key.endswith("_id") or key == "organisation_id", (path, key)
            assert not any(word in key for word in _NEVER_IN_KEYS), (path, key)
            if any(word in key for word in _COUNTS_ONLY):
                assert value is None or type(value) in (int, float), (path, key, value)
    # The planted rows were read: both organisations show, and the run's model is on the board.
    insights, quality = replies[INSIGHTS].json(), replies[QUALITY].json()
    assert {row["organisation_id"] for row in insights["daily"]} >= {a, b}
    assert {row["organisation_id"] for row in insights["organisations"]} >= {a, b}
    assert {row["organisation_id"] for row in quality["organisations"]} >= {a, b}
    assert MODEL in {item["model"] for item in replies[BOARD].json()["items"]}
    assert _platform(operator) == audited  # reads write no platform_audit row


def test_a_disabled_operator_gets_401_from_every_insights_route(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    _plant(api, "C")
    for path in (INSIGHTS, QUALITY, BOARD):
        assert client.get(path).status_code == 200, path
    operator.sql(_DISABLED, d=True)
    for path in (INSIGHTS, QUALITY, BOARD):
        reply = client.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path
