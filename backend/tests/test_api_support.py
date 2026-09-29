"""Shared helpers for the API tests (no tests in here).

It is named ``test_api_*`` only because that is the file pattern the API
builder owns (FULL-BUILD-SPEC section 10); pytest collects it and finds nothing.

* ``ApiFakeStorage`` / ``ApiFakeQueue``: the in-memory fakes from tests/fakes.py
  plus the extra methods the API calls (page images, ping).
* ``FakeAnswers``: returns a fixed ``Answer`` JSON and records the questions it
  was asked, so a test can check the question it was given.
* ``TestClient``: re-exported, so every API test imports it from one place.
* ``Api``: a TestClient on a real test database, with helpers to add users,
  sign in and add document rows directly with SQL (as the pipeline would).
"""

import hashlib
import io
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient as TestClient
from sqlalchemy import text

from kaagaz.api.app import create_app
from kaagaz.api.auth import hash_password
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.settings import Settings
from tests.db_support import insert_folder, insert_tag, new_customer, tag_document
from tests.fakes import MemoryQueue, MemoryStorage
from tests.settings_factory import make_settings

CSRF = {"X-Requested-With": "kaagaz"}
PASSWORD = "correct horse battery"  # made up, 12+ characters
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 200
NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


class ApiFakeStorage(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.page_images: dict[tuple[str, str, int], bytes] = {}
        self.down = False

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes:
        try:
            return self.page_images[(customer_id, document_id, page_no)]
        except KeyError:
            raise BlobNotFound(document_id) from None

    def delete(self, customer_id: str, document_id: str) -> None:
        super().delete(customer_id, document_id)
        for key in [k for k in self.page_images if k[:2] == (customer_id, document_id)]:
            del self.page_images[key]

    def ping(self) -> None:
        if self.down:
            raise BackendUnavailable("storage")

    def has(self, customer_id: str, document_id: str) -> bool:
        try:
            self.get(customer_id, document_id)
        except BlobNotFound:
            return False
        return True


class ApiFakeQueue(MemoryQueue):
    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def ping(self) -> None:
        if self.down:
            raise BackendUnavailable("queue")


def answer_json(*, status: str = "answered", label: str | None = "verified") -> dict[str, Any]:
    """An ``Answer`` as frontend/lib/types.ts defines it."""
    answered = status == "answered"
    return {
        "status": status,
        "label": label if answered else None,
        "sentences": [{"text": "The total is 1,200.", "citations": [1]}] if answered else [],
        "citations": [],
        "closest_documents": [],
        "route": "search",
        "normalized_question": "what is the total",
        "model_used": False,
    }


@dataclass(frozen=True)
class _FixedAnswer:
    data: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass
class FakeAnswers:
    reply: dict[str, Any] = field(default_factory=answer_json)
    asked: list[tuple[str, str]] = field(default_factory=list)

    def answer(self, customer_id: str, question: str) -> _FixedAnswer:
        self.asked.append((customer_id, question))
        return _FixedAnswer(self.reply)


@dataclass
class Signed:
    """A signed-in user and their client."""

    client: TestClient
    customer_id: str
    user_id: str
    email: str


class Api:
    def __init__(
        self,
        db: Database,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or make_settings()
        self.storage = ApiFakeStorage()
        self.queue = ApiFakeQueue()
        self.answers = FakeAnswers()
        self.app = create_app(
            self.settings,
            db=db,
            storage=self.storage,
            queue=self.queue,
            answers=self.answers,
            clock=clock or (lambda: NOW),
        )

    def client(self) -> TestClient:
        return TestClient(self.app)

    # --- people ------------------------------------------------------------------

    def customer(self, name: str = "Test customer") -> tuple[str, str]:
        """A new customer and its admin (whose password is not usable)."""
        return new_customer(self.db, name=name)

    def add_user(self, customer_id: str, role: str, *, password: str = PASSWORD) -> tuple[str, str]:
        email = f"{role}-{uuid.uuid4().hex[:12]}@example.test"
        with self.db.customer(customer_id) as conn:
            row = users_sql.create_user(
                conn,
                customer_id,
                email=email,
                display_name=f"Test {role}",
                role=role,
                password_hash=hash_password(password),
            )
        return str(row["id"]), email

    def sign_in(self, customer_id: str, role: str = "member") -> Signed:
        user_id, email = self.add_user(customer_id, role)
        client = self.client()
        reply = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return Signed(client, customer_id, user_id, email)

    def new_signed(self, role: str = "member") -> Signed:
        customer_id, _ = self.customer()
        return self.sign_in(customer_id, role)

    # --- documents, as the pipeline would leave them ------------------------------

    def add_document(
        self,
        customer_id: str,
        *,
        status: str = "ready",
        detected_type: str = "pdf",
        name: str = "invoice.pdf",
        uploaded_by: str | None = None,
        data: bytes = PDF_BYTES,
        failure_code: str | None = None,
    ) -> str:
        document_id = str(uuid.uuid4())
        with self.db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes,"
                    " detected_type, status, failure_code, uploaded_by, page_count, family_id)"
                    " VALUES (:d, :c, :n, :h, :s, :t, :st, :fc, :by, 1, :d)"
                ),
                {
                    "d": document_id,
                    "c": customer_id,
                    "n": name,
                    "h": hashlib.sha256(document_id.encode()).hexdigest(),
                    "s": len(data),
                    "t": detected_type,
                    "st": status,
                    "fc": failure_code,
                    "by": uploaded_by,
                },
            )
        self.storage.put(customer_id, document_id, io.BytesIO(data))
        return document_id

    def add_page(
        self, customer_id: str, document_id: str, page_no: int = 1, *, unit: str = "pt", height: float = 842.0
    ) -> None:
        with self.db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image)"
                    " VALUES (:c, :d, :n, 595, :h, :u, :img)"
                ),
                {
                    "c": customer_id,
                    "d": document_id,
                    "n": page_no,
                    "h": height,
                    "u": unit,
                    "img": unit != "cells",
                },
            )

    def add_value(
        self,
        customer_id: str,
        document_id: str,
        *,
        field_name: str = "total_amount",
        value: str = "1,200",
        raw: str = "1,200",
        second: str | None = "1,250",
        label: str = "needs_review",
        reason: str = "readers_disagree",
        page_no: int = 1,
    ) -> str:
        with self.db.customer(customer_id) as conn:
            value_id: object = conn.execute(
                text(
                    "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
                    " value_raw, second_reading, page_no, region, reader, confidence, label, label_reason)"
                    " VALUES (:c, :d, 'document', :f, :v, :r, :s, :p, CAST(:region AS jsonb),"
                    " 'pdfplumber', NULL, :l, :lr) RETURNING id"
                ),
                {
                    "c": customer_id,
                    "d": document_id,
                    "f": field_name,
                    "v": value,
                    "r": raw,
                    "s": second,
                    "p": page_no,
                    "region": json.dumps({"kind": "box", "page": page_no, "box": [0.1, 0.2, 0.3, 0.25]}),
                    "l": label,
                    "lr": reason,
                },
            ).scalar_one()
        return str(value_id)

    def add_reader_output(self, customer_id: str, document_id: str, spans: list[dict[str, Any]]) -> None:
        with self.db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO reader_outputs"
                    " (customer_id, document_id, role, reader, reader_version, output)"
                    " VALUES (:c, :d, 'first', 'csv', '1', CAST(:o AS jsonb))"
                ),
                {"c": customer_id, "d": document_id, "o": json.dumps(spans)},
            )

    def audit_actions(self, customer_id: str) -> list[str]:
        with self.db.customer(customer_id) as conn:
            rows = conn.execute(
                text("SELECT action FROM audit_log WHERE customer_id = :c ORDER BY id"), {"c": customer_id}
            ).all()
        return [str(row[0]) for row in rows]

    def audit_rows(self, customer_id: str) -> list[tuple[str, str | None, str | None, dict[str, Any]]]:
        """Every audit row of this customer, oldest first: (action, target_type, target_id, details)."""
        with self.db.customer(customer_id) as conn:
            rows = conn.execute(
                text(
                    "SELECT action, target_type, target_id, details FROM audit_log"
                    " WHERE customer_id = :c ORDER BY id"
                ),
                {"c": customer_id},
            ).all()
        return [(str(row[0]), row[1], row[2], dict(row[3])) for row in rows]

    # --- the library: folders and tags, straight into the tables (PRD 1 WP1.5) ---------

    def add_folder(self, customer_id: str, user_id: str, name: str, parent_id: str | None = None) -> str:
        with self.db.customer(customer_id) as conn:
            return insert_folder(conn, customer_id, user_id, name, parent_id)

    def add_tag(self, customer_id: str, user_id: str, name: str) -> str:
        with self.db.customer(customer_id) as conn:
            return insert_tag(conn, customer_id, user_id, name)

    def tag_document(self, customer_id: str, document_id: str, tag_id: str) -> None:
        with self.db.customer(customer_id) as conn:
            tag_document(conn, customer_id, document_id, tag_id)
