"""The whole product, once through, against a real PostgreSQL.

Upload through the API, process each queued job with the production processor
(real readers, masking, pieces, values, labels), then ask in the chat. Only
file storage and the queue are in-memory stand-ins, and the virus scanner is
the laptop EICAR scanner. No AI model: search runs without meaning vectors and
answers quote the evidence.

It checks the promises end to end: the Hinglish question gets a number from a
fixed query with a cell citation, a text question gets a quoted line with a box
on the page, a question the documents cannot answer says so, and another
customer asking the same question gets nothing.
"""

from datetime import UTC, datetime

import pytest

from kaagaz.adapters.scanner import EicarDevScanner
from kaagaz.api.app import create_app
from kaagaz.api.auth import hash_password
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.pipeline import build_processor
from tests.db_support import new_customer
from tests.pdf_builder import Page, Text, make_pdf
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, ApiFakeQueue, ApiFakeStorage, TestClient

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)  # "last month" is August 2026
HINGLISH = "Kiska profit sabse jyada tha last month?"
INVOICE_QUESTION = "What is the grand total of invoice INV-1042?"

REGISTER = (
    b"Name,Month,Profit\n"
    b"Sharma Traders,Aug 2026,120000\n"
    b"Mehta Textiles,Aug 2026,95000\n"
    b"Sharma Traders,Jul 2026,300000\n"
)

INVOICE = make_pdf(
    [
        Page(
            texts=[
                Text(72, 720, "Tax Invoice", size=16),
                Text(72, 690, "Invoice No: INV-1042"),
                Text(72, 670, "Seller: Gupta Hardware"),
                Text(72, 650, "Grand Total: Rs. 12,500.00"),
            ]
        )
    ]
)


class PipelineStorage(ApiFakeStorage):
    """The API's fake storage, plus the page pictures the pipeline writes."""

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None:
        self.page_images[(customer_id, document_id, page_no)] = png


def _sign_in(db: Database, app_client: TestClient, customer_id: str) -> None:
    email = f"member-{customer_id[:8]}@example.test"
    with db.customer(customer_id) as conn:
        users_sql.create_user(
            conn,
            customer_id,
            email=email,
            display_name="Test member",
            role="member",
            password_hash=hash_password(PASSWORD),
        )
    reply = app_client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
    assert reply.status_code == 200, reply.text


def test_upload_process_and_ask(db: Database) -> None:
    settings = make_settings()
    storage, queue = PipelineStorage(), ApiFakeQueue()
    app = create_app(settings, db=db, storage=storage, queue=queue, clock=lambda: NOW)
    processor = build_processor(
        settings=settings, db=db, storage=storage, scanner=EicarDevScanner(), model=None
    )

    customer_a, _ = new_customer(db, name="Sharma Traders")
    customer_b, _ = new_customer(db, name="Mehta Textiles")
    a, b = TestClient(app), TestClient(app)
    _sign_in(db, a, customer_a)
    _sign_in(db, b, customer_b)

    for name, data, kind in (
        ("profit-register.csv", REGISTER, "text/csv"),
        ("invoice.pdf", INVOICE, "application/pdf"),
    ):
        reply = a.post("/api/documents", files={"file": (name, data, kind)}, headers=CSRF)
        assert reply.status_code == 201, reply.text

    assert len(queue.jobs) == 2
    for job in queue.jobs:
        processor(job, lambda: None)

    listed = a.get("/api/documents").json()
    assert sorted(d["status"] for d in listed["items"]) == ["ready", "ready"]

    # 1. Hinglish, a number: the fixed query route, cited to the exact cells.
    reply = a.post("/api/chat", json={"question": HINGLISH}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    answer = reply.json()["answer"]["answer"]
    assert answer["status"] == "answered"
    assert answer["route"] == "fixed_query"
    sentence = " ".join(s["text"] for s in answer["sentences"])
    assert "Sharma Traders" in sentence
    assert "1,20,000" in sentence  # August only, Indian grouping; July's 3,00,000 is not used
    assert answer["label"] == "verified"  # read straight from the cells
    assert all(c["regions"] and c["regions"][0]["kind"] == "cell" for c in answer["citations"])
    assert all(s["citations"] for s in answer["sentences"])

    # 2. A text question: quoted from the PDF, with a box on page 1.
    reply = a.post("/api/chat", json={"question": INVOICE_QUESTION}, headers=CSRF)
    answer = reply.json()["answer"]["answer"]
    assert answer["status"] == "answered", answer
    quoted = [s["text"] for s in answer["sentences"]]
    assert "Grand Total: Rs. 12,500.00" in quoted
    assert "Tax Invoice" not in quoted  # a heading is context, not the answer
    (citation,) = answer["citations"]
    assert citation["page_no"] == 1
    # The exact spot: only the lines quoted are highlighted, lowest (Grand Total) last.
    assert all(r["kind"] == "box" for r in citation["regions"])
    assert len(citation["regions"]) == len(quoted)
    grand_total_box = citation["regions"][-1]["box"]
    assert grand_total_box[1] > 0.15  # below the heading, which sits near the top

    # 3. Not in the documents: said so, never guessed.
    reply = a.post("/api/chat", json={"question": "Who is the CEO of Tata Motors?"}, headers=CSRF)
    answer = reply.json()["answer"]["answer"]
    assert answer["status"] == "not_found"
    assert answer["sentences"] == []

    # 4. Another customer asking the same questions gets nothing of A's.
    for question in (HINGLISH, INVOICE_QUESTION):
        reply = b.post("/api/chat", json={"question": question}, headers=CSRF)
        answer = reply.json()["answer"]["answer"]
        assert answer["status"] == "not_found"
        assert answer["citations"] == []
        assert answer["closest_documents"] == []
    assert b.get("/api/documents").json()["total"] == 0
