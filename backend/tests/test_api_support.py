"""Shared helpers for the API tests (no tests in here).

It is named ``test_api_*`` only because that is the file pattern the API
builder owns (FULL-BUILD-SPEC section 10); pytest collects it and finds nothing.

* ``ApiFakeStorage`` / ``ApiFakeQueue``: the in-memory fakes from tests/fakes.py
  plus the extra methods the API calls (page images, ping).
* ``FakeAnswers``: returns a fixed ``Answer`` JSON and records the questions it
  was asked, so a test can check the question it was given.
* ``TestClient``: re-exported, so every API test imports it from one place.
* ``MovingClock``: the app's clock. Tests move it forward instead of sleeping.
* ``Api``: a TestClient on a real test database, with helpers to add users,
  sign in (on the normal screen, or on the admin screen with a 6-digit code)
  and add document rows directly with SQL (as the pipeline would).
* ``route_calls``: every API route with its path filled in, for tests that walk
  all routes (the admin-session and the password-change gates).
"""

import functools
import hashlib
import io
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient as TestClient
from sqlalchemy import RowMapping, text

from kaagaz.ai.client import ModelClient
from kaagaz.answering.memory import Memory
from kaagaz.answering.rewrite import Turn
from kaagaz.api import totp
from kaagaz.api.app import create_app
from kaagaz.api.auth import hash_password
from kaagaz.api.ports import Answers
from kaagaz.api.secret_box import SecretBox
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.search.scope import Scope
from kaagaz.settings import Settings
from tests.db_support import insert_document, insert_folder, insert_tag, new_customer, tag_document
from tests.fakes import MemoryQueue, MemoryStorage
from tests.settings_factory import make_settings

CSRF = {"X-Requested-With": "kaagaz"}
PASSWORD = "correct horse battery"  # made up, 12+ characters
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 200
NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


def password_hash_of(password: str) -> str:
    """The stored hash for a user a helper adds. PASSWORD's is made once per test session
    (scrypt takes about a quarter of a second on purpose), so those users share one salt;
    any other password is hashed fresh. Only the helpers use this: the product's own
    hashing still makes a new salt every time (test_api_admin.py checks)."""
    return _password_hash() if password == PASSWORD else hash_password(password)


@functools.cache
def _password_hash() -> str:
    return hash_password(PASSWORD)


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

    def depth(self) -> dict[str, int]:
        """Every job queued so far counts as waiting in the main lane."""
        if self.down:
            raise BackendUnavailable("queue")
        return {"express": 0, "main": len(self.jobs)}


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
    scopes: list[Scope] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)  # PRD 5 M3: the memory each question got
    remembered: Memory | None = None  # what ``remember`` gives back
    summarised: list[tuple[list[Turn], Memory, list[str]]] = field(default_factory=list)

    def answer(
        self,
        customer_id: str,
        question: str,
        *,
        scope: Scope,
        history: Sequence[Turn],
        user_id: str,
        memory: Memory,
    ) -> _FixedAnswer:
        self.asked.append((customer_id, question))
        self.scopes.append(scope)
        self.memories.append(memory)
        return _FixedAnswer(self.reply)

    def remember(self, turns: Sequence[Turn], memory: Memory, cited: Sequence[str]) -> Memory | None:
        self.summarised.append((list(turns), memory, list(cited)))
        return self.remembered


@dataclass
class Signed:
    """A signed-in user and their client. ``scope`` is the session's: "app" from
    /login, "admin" from /admin/login. ``totp_secret`` is set for an enrolled admin."""

    client: TestClient
    customer_id: str
    user_id: str
    email: str
    scope: str = "app"
    totp_secret: bytes | None = None


@dataclass(frozen=True)
class EnrolledAdmin:
    """An admin whose two-step code is set up (by SQL, as enrolment would leave it)."""

    user_id: str
    email: str
    secret: bytes


@dataclass
class MovingClock:
    """The app's clock: it stands still until a test moves it forward."""

    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        if seconds < 0:
            raise ValueError("the clock only moves forward")
        self.now += timedelta(seconds=seconds)
        return self.now


class Api:
    def __init__(
        self,
        db: Database,
        *,
        settings: Settings | None = None,
        clock: MovingClock | None = None,
        queue: ApiFakeQueue | None = None,
        model: ModelClient | None = None,
        answers: Answers | None = None,
    ) -> None:
        """``answers``: the real AnswerService for a test that needs one; FakeAnswers otherwise."""
        self.db = db
        self.settings = settings or make_settings()
        self.clock = clock or MovingClock()
        self.storage = ApiFakeStorage()
        self.queue = queue or ApiFakeQueue()
        self.answers = FakeAnswers()
        self.app = create_app(
            self.settings,
            db=db,
            storage=self.storage,
            queue=self.queue,
            answers=self.answers if answers is None else answers,
            clock=self.clock,
            model=model,
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
                password_hash=password_hash_of(password),
                must_change_password=False,
            )
        return str(row["id"]), email

    def sign_in(self, customer_id: str, role: str = "member") -> Signed:
        """A new user of this role, signed in on the normal screen (an app session)."""
        user_id, email = self.add_user(customer_id, role)
        client = self.client()
        reply = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return Signed(client, customer_id, user_id, email)

    def new_signed(self, role: str = "member") -> Signed:
        customer_id, _ = self.customer()
        return self.sign_in(customer_id, role)

    def enrol_admin(self, customer_id: str) -> EnrolledAdmin:
        """A new admin with a two-step code already set up, written by SQL the way
        ``POST /auth/admin/enrol`` leaves it (the secret sealed with SecretBox)."""
        user_id, email = self.add_user(customer_id, "admin")
        secret = totp.new_secret()
        sealed = SecretBox(self.settings.totp_encryption_key).seal(secret, aad=user_id)
        with self.db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE users SET totp_secret_enc = :sealed, totp_enabled_at = :now"
                    " WHERE customer_id = :c AND id = :u"
                ),
                {"sealed": sealed, "now": self.clock(), "c": customer_id, "u": user_id},
            )
        return EnrolledAdmin(user_id, email, secret)

    def step(self, offset: int = 0) -> int:
        """The current 30-second step of the clock, plus ``offset`` steps."""
        return int(self.clock().timestamp()) // totp.STEP_SECONDS + offset

    def code(self, secret: bytes, offset: int = 0) -> str:
        """The 6-digit code an authenticator app shows now (or ``offset`` steps away)."""
        return totp.code_at(secret, self.step(offset))

    def admin_login(
        self, client: TestClient, email: str, *, password: str = PASSWORD, code: str | None = None
    ) -> httpx.Response:
        body: dict[str, str] = {"email": email, "password": password}
        if code is not None:
            body["code"] = code
        return client.post("/api/auth/admin/login", json=body, headers=CSRF)

    def admin_sign_in(self, customer_id: str) -> Signed:
        """A new enrolled admin, signed in on the admin screen with this step's code."""
        admin = self.enrol_admin(customer_id)
        client = self.client()
        reply = self.admin_login(client, admin.email, code=self.code(admin.secret))
        assert reply.status_code == 200, reply.text
        return Signed(client, customer_id, admin.user_id, admin.email, "admin", admin.secret)

    def new_admin_of(self, customer_id: str) -> Signed:
        """Another admin of this customer, with 2FA on, signed in on the admin screen."""
        return self.admin_sign_in(customer_id)

    def new_admin(self, name: str = "Test customer") -> Signed:
        """A new customer and an admin signed in on the admin screen."""
        customer_id, _ = self.customer(name)
        return self.admin_sign_in(customer_id)

    def user_state(self, customer_id: str, user_id: str) -> RowMapping:
        """The sign-in columns of one user, read straight from the database."""
        with self.db.customer(customer_id) as conn:
            return (
                conn.execute(
                    text(
                        "SELECT password_hash, failed_logins, locked_until, session_version,"
                        " must_change_password, totp_secret_enc, totp_enabled_at, totp_last_step,"
                        " disabled, role"
                        " FROM users WHERE customer_id = :c AND id = :u"
                    ),
                    {"c": customer_id, "u": user_id},
                )
                .mappings()
                .one()
            )

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
        page_count: int | None = 1,
        **columns: Any,
    ) -> str:
        """One document row (through ``db_support.insert_document``) and its stored file.
        Any other ``documents`` column can be given by name, for example ``folder_id``."""
        document_id = str(columns.pop("id", uuid.uuid4()))
        with self.db.customer(customer_id) as conn:
            insert_document(
                conn,
                customer_id,
                id=document_id,
                display_name=name,
                sha256=hashlib.sha256(document_id.encode()).hexdigest(),
                size_bytes=len(data),
                detected_type=detected_type,
                status=status,
                failure_code=failure_code,
                uploaded_by=uploaded_by,
                page_count=page_count,
                **columns,
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

    def add_reader_output(
        self,
        customer_id: str,
        document_id: str,
        spans: list[dict[str, Any]],
        *,
        role: str = "first",
        reader: str = "csv",
        reader_version: str = "1",
    ) -> None:
        with self.db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO reader_outputs"
                    " (customer_id, document_id, role, reader, reader_version, output)"
                    " VALUES (:c, :d, :role, :reader, :version, CAST(:o AS jsonb))"
                ),
                {
                    "c": customer_id,
                    "d": document_id,
                    "role": role,
                    "reader": reader,
                    "version": reader_version,
                    "o": json.dumps(spans),
                },
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


def wrong_code(api: Api, secret: bytes) -> str:
    """A well-formed 6-digit code that matches none of the steps accepted right now."""
    accepted = {api.code(secret, offset) for offset in (-1, 0, 1)}
    return next(code for code in ("000000", "111111", "222222", "333333") if code not in accepted)


# --- walking every route ------------------------------------------------------------

ANY_ID = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"  # a well-formed id nothing has


def route_calls(app: FastAPI, prefix: str = "/api") -> list[tuple[str, str]]:
    """Every (method, path) the app serves under ``prefix``, with each path
    parameter filled in: 1 for a number, a well-formed unknown id otherwise.

    ``iter_route_contexts`` is how FastAPI itself (its OpenAPI builder) walks
    the routes of included routers, with their prefixes applied."""
    calls: list[tuple[str, str]] = []
    for context in iter_route_contexts(app.routes):
        route, path = context.original_route, context.path
        if not isinstance(route, APIRoute) or path is None or not path.startswith(prefix):
            continue
        for param in route.dependant.path_params:
            value = "1" if param.field_info.annotation is int else ANY_ID
            path = path.replace("{" + param.name + "}", value)
        calls.extend((method, path) for method in sorted(context.methods or ()))
    return sorted(calls)


def call(client: TestClient, method: str, path: str) -> httpx.Response:
    """One request with the CSRF header and an empty JSON body where one can go."""
    if method in ("GET", "DELETE"):
        return client.request(method, path, headers=CSRF)
    return client.request(method, path, json={}, headers=CSRF)
