"""An English question about a Marathi 7/12, end to end on PostgreSQL (council 2 Oct
2026, PR2): CSV cells with Devanagari digits, processed with no AI model, as in
test_end_to_end_db.py. Only the glossary's word list finds the owner's row; the answer
quotes the name from its cell, needs review (the gate passed through the glossary)."""

from typing import Any

import pytest

from kaagaz.adapters.scanner import EicarDevScanner
from kaagaz.answering import glossary
from kaagaz.api.app import create_app
from kaagaz.db.engine import Database
from kaagaz.pipeline import build_processor
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, ApiFakeQueue, TestClient
from tests.test_end_to_end_db import NOW, PipelineStorage, _sign_in

pytestmark = pytest.mark.db

QUESTION = "Who is the owner of the land?"
ROWS = (
    "गाव,वडगाव",
    "तालुका,हवेली",
    "जिल्हा,पुणे",
    "गट क्रमांक,४७",
    "एकूण क्षेत्र (हे.आर),१.२०",
    "जमीन मालकाचे नाव,रामचंद्र गणपत पाटील",
)
SATBARA = ("\n".join(ROWS) + "\n").encode()


def _two_customers(db: Database) -> tuple[TestClient, TestClient]:
    """Customer A with the processed 7/12, and customer B with nothing; both signed in."""
    settings = make_settings()
    storage, queue = PipelineStorage(), ApiFakeQueue()
    app = create_app(settings, db=db, storage=storage, queue=queue, clock=lambda: NOW)
    processor = build_processor(
        settings=settings, db=db, storage=storage, scanner=EicarDevScanner(), model=None
    )
    customer_a, _ = new_customer(db, name="Patil Farms")
    customer_b, _ = new_customer(db, name="Shinde Traders")
    a, b = TestClient(app), TestClient(app)
    _sign_in(db, a, customer_a)
    _sign_in(db, b, customer_b)
    reply = a.post("/api/documents", files={"file": ("satbara.csv", SATBARA, "text/csv")}, headers=CSRF)
    assert reply.status_code == 201, reply.text
    for job in queue.jobs:
        processor(job, lambda: None)
    assert [d["status"] for d in a.get("/api/documents").json()["items"]] == ["ready"]
    return a, b


def _ask(client: TestClient, question: str) -> dict[str, Any]:
    reply = client.post("/api/chat", json={"question": question}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    answer: dict[str, Any] = reply.json()["answer"]["answer"]
    return answer


def test_an_english_question_is_answered_from_a_marathi_land_record(db: Database) -> None:
    a, _ = _two_customers(db)

    answer = _ask(a, QUESTION)

    assert (answer["status"], answer["route"], answer["label"]) == ("answered", "search", "needs_review")
    assert [s["text"] for s in answer["sentences"]] == ["A: जमीन मालकाचे नाव | B: रामचंद्र गणपत पाटील"]
    (citation,) = answer["citations"]
    assert "रामचंद्र गणपत पाटील" in citation["snippet"]
    assert citation["regions"] and all(region["kind"] == "cell" for region in citation["regions"])
    # g27's near miss: only gat ४७ is there.
    assert _ask(a, "Who owns gat number 48?")["status"] == "not_found"


@pytest.mark.isolation
def test_another_customer_asking_gets_nothing_of_it(db: Database) -> None:
    a, b = _two_customers(db)

    assert _ask(a, QUESTION)["status"] == "answered"  # the positive control
    answer = _ask(b, QUESTION)
    assert (answer["status"], answer["citations"], answer["closest_documents"]) == ("not_found", [], [])


def test_without_the_glossary_the_question_is_not_found(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = _two_customers(db)
    monkeypatch.setattr(glossary, "EQUIVALENTS", {})

    assert _ask(a, QUESTION)["status"] == "not_found"
