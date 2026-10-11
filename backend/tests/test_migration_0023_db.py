"""Migration 0023 (model choice and AI spend, council 11 Oct 2026): the model catalogue, the
organisations' models, the budget, the documents' chosen model and payer, the ledger and
the operator's five functions.

Up, down (0022's schema and comments exactly) and up again, with ``read_requested_by``
backfilled from ``uploaded_by``; every new function, index and policy carries a comment;
each new CHECK refuses a bad row; the spend outlives a document deleted for good, a chat
deleted and a user removed (the ids set to NULL, the cost kept); an organisation's purge
(0018, unchanged) still works with ledger rows and takes them. Grants, policies and the
functions' pinned signatures: test_isolation_schema; customer separation:
test_isolation_model_usage and test_isolation_customer_models.

The helpers at the top are shared by the other 0023 tests: catalogue rows, operators and
organisations' models are written as the table owner (as verify-models, the CLI and the
operator's functions do); ledger rows through the app role with the customer set.
"""

import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.fakes import MemoryStorage
from tests.test_migration_0004_db import _current, _head
from tests.test_migration_0007_db import _dictionary
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
PROMPT = READER_PROMPT_VERSION  # the reader's prompt version the test models are verified for
VERTEX = ("google-vertex",)
NEW_TABLES = {"model_catalog", "customer_models", "model_usage"}
FUNCTIONS = {
    "kaagaz_op_models",
    "kaagaz_op_set_model_enabled",
    "kaagaz_op_org_models",
    "kaagaz_op_set_org_models",
    "kaagaz_op_usage",
}
NEW_INDEXES = {
    "customer_models_default",
    "model_usage_period",
    "model_usage_document",
    "model_usage_conversation",
}
NEW_COLUMNS = {
    ("customers", "ai_budget_usd"),
    ("documents", "read_model"),
    ("documents", "read_requested_by"),
    ("model_catalog", "enabled_by"),
    ("customer_models", "is_default_scan"),
    ("model_usage", "cost_estimated"),
}

# --- shared helpers --------------------------------------------------------------------

_OPERATOR = (
    "INSERT INTO operators (email, display_name, password_hash, totp_secret_enc, totp_enabled_at, disabled)"
    " VALUES (:e, 'Ops', 'not-a-real-hash', 'sealed', CASE WHEN :confirmed THEN now() END, :disabled)"
    " RETURNING id"
)
_MODEL = (
    "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers, eligible,"
    " verified_prompt_version, verified_at, tokens_per_page_in, tokens_per_page_out, enabled_at, enabled_by)"
    " VALUES (:id, :name, :pin, :pout, CAST(:providers AS text[]), :eligible, CAST(:version AS text),"
    " CASE WHEN CAST(:version AS text) IS NULL THEN NULL ELSE now() END, :tin, :tout,"
    " CASE WHEN CAST(:by AS uuid) IS NULL THEN NULL ELSE now() END, CAST(:by AS uuid))"
)
_ALLOW = "INSERT INTO customer_models (customer_id, model, is_default_scan) VALUES (:c, :m, :d)"
_USAGE = text(
    "INSERT INTO model_usage (customer_id, user_id, document_id, conversation_id, kind, model, provider_name,"
    " cached, calls, input_tokens, output_tokens, reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:customer_id, :user_id, :document_id, :conversation_id, :kind, :model, :provider_name,"
    " :cached, :calls, :input_tokens, :output_tokens, :reasoning_tokens, :cost_usd, :cost_estimated,"
    " COALESCE(CAST(:created_at AS timestamptz), now())) RETURNING id"
)
_USAGE_DEFAULTS: dict[str, Any] = {
    "user_id": None,
    "document_id": None,
    "conversation_id": None,
    "kind": "scan",
    "model": "google/gemini-3.1-pro-preview",
    "provider_name": None,
    "cached": False,
    "calls": 1,
    "input_tokens": 100,
    "output_tokens": 50,
    "reasoning_tokens": 0,
    "cost_usd": 0.01,
    "cost_estimated": False,
    "created_at": None,
}
_CHAT = text("INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, 'Q') RETURNING id")


def new_operator(db: Database, *, confirmed: bool = True, disabled: bool = False) -> str:
    """An operator whose two-step secret is confirmed (or still pending), as the owner."""
    email = f"op-{uuid.uuid4().hex[:12]}@example.test"
    return str(_owner(db, _OPERATOR, {"e": email, "confirmed": confirmed, "disabled": disabled})[0][0])


def catalog_model(
    db: Database,
    *,
    name: str = "Test model",
    providers: Sequence[str] = VERTEX,
    eligible: bool = True,
    verified: str | None = PROMPT,
    enabled: bool = True,
    price_in: float = 2,
    price_out: float = 12,
    tokens: tuple[int, int] | None = (1000, 500),
) -> str:
    """A new catalogue model with a unique id: eligible, verified for PROMPT and enabled by
    a new operator unless told otherwise. Returns its id."""
    model = f"test-{uuid.uuid4().hex[:10]}/model-1"
    params = {
        "id": model,
        "name": name,
        "pin": price_in,
        "pout": price_out,
        "providers": list(providers),
        "eligible": eligible,
        "version": verified,
        "tin": None if tokens is None else tokens[0],
        "tout": None if tokens is None else tokens[1],
        "by": new_operator(db) if enabled else None,
    }
    _owner(db, _MODEL, params)
    return model


def allow(db: Database, customer_id: str, models: Sequence[str], default: str | None) -> None:
    """The operator allowed ``models`` to ``customer_id``, ``default`` its default scan model."""
    for model in models:
        _owner(db, _ALLOW, {"c": customer_id, "m": model, "d": model == default})


def add_usage(conn: Connection, customer_id: str, **columns: Any) -> int:
    """One ledger row through the app role (``conn`` from ``Database.customer``): the
    defaults above, with ``columns`` over them (a key not among them is a TypeError)."""
    unknown = set(columns) - set(_USAGE_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown model_usage columns: {sorted(unknown)}")
    return int(conn.execute(_USAGE, {**_USAGE_DEFAULTS, **columns, "customer_id": customer_id}).scalar_one())


def new_chat(conn: Connection, customer_id: str, user_id: str) -> str:
    return str(conn.execute(_CHAT, {"c": customer_id, "u": user_id}).scalar_one())


def as_owner(db: Database, statement: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Rows as the table owner, which row level security does not filter."""
    return [tuple(row) for row in _owner(db, statement, params or {})]


# --- the migration -----------------------------------------------------------------------


def test_0023_up_down_up_and_the_payer_is_backfilled_from_the_uploader(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0022")
    customer, admin = new_customer(database)
    with database.customer(customer) as conn:
        uploaded = insert_document(conn, customer, uploaded_by=admin)
        by_nobody = insert_document(conn, customer)
    at_0022, comments_0022 = m0002._schema(database), _dictionary(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head, comments_head = m0002._schema(database), _dictionary(database)
    assert m0002._names(at_head, "relations") >= NEW_TABLES | NEW_INDEXES
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    assert set(comments_head) >= NEW_COLUMNS | {(table, None) for table in NEW_TABLES}
    with database.customer(customer) as conn:
        billed = conn.execute(
            text("SELECT CAST(id AS text), CAST(read_requested_by AS text), read_model FROM documents"),
        ).all()
    assert sorted(tuple(row) for row in billed) == sorted([(uploaded, admin, None), (by_nobody, None, None)])

    m0002._downgrade(url, "0022")
    assert m0002._schema(database) == at_0022
    assert _dictionary(database) == comments_0022
    migrate(url, "head")
    assert m0002._schema(database) == at_head
    assert _dictionary(database) == comments_head


def test_every_new_function_index_and_policy_has_a_comment(db: Database) -> None:
    comments = as_owner(
        db,
        "SELECT p.proname::text, obj_description(p.oid, 'pg_proc') FROM pg_proc p"
        " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(CAST(:f AS text[]))"
        " UNION ALL SELECT c.relname::text, obj_description(c.oid, 'pg_class') FROM pg_class c"
        " WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(CAST(:i AS text[]))"
        " UNION ALL SELECT p.polname || ' ON ' || CAST(p.polrelid AS regclass),"
        " obj_description(p.oid, 'pg_policy') FROM pg_policy p"
        " WHERE p.polrelid IN ('model_catalog'::regclass, 'customer_models'::regclass,"
        " 'model_usage'::regclass)",
        {"f": sorted(FUNCTIONS), "i": sorted(NEW_INDEXES)},
    )
    assert len(comments) == len(FUNCTIONS) + len(NEW_INDEXES) + 5  # 3 catalogue policies, 2 customer_only
    assert [name for name, comment in comments if not (comment or "").strip()] == []


@pytest.mark.parametrize(
    ("model", "price", "providers", "efforts", "version", "verified", "constraint"),
    [
        ("Google/gemini-x", 1, [], [], None, False, "model_catalog_id_check"),  # upper-case author
        ("google/gemini-x:free", 1, [], [], None, False, "model_catalog_id_check"),  # a variant
        ("google/gemini-x", -1, [], [], None, False, "model_catalog_price_in_usd_per_m_check"),
        ("google/gemini-x", 1, ["Azure"], [], None, False, "model_catalog_providers_check"),
        ("google/gemini-x", 1, [None], [], None, False, "model_catalog_providers_check"),
        ("google/gemini-x", 1, ["x"] * 17, [], None, False, "model_catalog_providers_check"),
        ("google/gemini-x", 1, [], ["e" * 17], None, False, "model_catalog_efforts_check"),
        ("google/gemini-x", 1, [], [], "G2", True, "model_catalog_verified_prompt_version_check"),
        ("google/gemini-x", 1, [], [], "g2", False, "model_catalog_check"),  # a version with no time
    ],
)
def test_a_bad_catalogue_row_is_refused(
    db: Database,
    model: str,
    price: float,
    providers: list[str | None],
    efforts: list[str],
    version: str | None,
    verified: bool,
    constraint: str,
) -> None:
    with pytest.raises(DBAPIError) as refused:
        _owner(
            db,
            "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers,"
            " efforts, verified_prompt_version, verified_at) VALUES (:id, 'M', :p, 0,"
            " CAST(:providers AS text[]), CAST(:efforts AS text[]), :v, CASE WHEN :verified THEN now() END)",
            {
                "id": model,
                "p": price,
                "providers": providers,
                "efforts": efforts,
                "v": version,
                "verified": verified,
            },
        )
    assert violation(refused.value) == ("23514", constraint)


def test_enabled_needs_its_operator_and_the_budget_must_be_above_zero(db: Database) -> None:
    model = catalog_model(db, enabled=False)
    with pytest.raises(DBAPIError) as refused:
        _owner(db, "UPDATE model_catalog SET enabled_at = now() WHERE id = :m", {"m": model})
    assert violation(refused.value) == ("23514", "model_catalog_check1")  # enabled_at without enabled_by
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused:
        _owner(db, "UPDATE customers SET ai_budget_usd = 0 WHERE id = :c", {"c": customer})
    assert violation(refused.value) == ("23514", "customers_ai_budget_usd_check")


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"calls": 0}, "model_usage_calls_check"),
        ({"kind": "Scan"}, "model_usage_kind_check"),
        ({"model": "gemini"}, "model_usage_model_check"),
        ({"provider_name": "a;b"}, "model_usage_provider_name_check"),
        ({"input_tokens": -1}, "model_usage_input_tokens_check"),
        ({"cost_usd": -0.01}, "model_usage_cost_usd_check"),
        ({"cost_usd": None, "cost_estimated": True}, "model_usage_check"),  # estimated from nothing
    ],
)
def test_a_bad_ledger_row_is_refused(db: Database, columns: dict[str, Any], constraint: str) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        add_usage(conn, customer, **columns)
    assert violation(refused.value) == ("23514", constraint)


def test_the_spend_outlives_a_document_deleted_for_good_a_chat_and_a_removed_user(db: Database) -> None:
    """FINAL 7: ON DELETE SET NULL (col) keeps each row and its cost; only the id goes."""
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        member = str(
            conn.execute(
                text(
                    "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
                    " VALUES (:c, :e, 'Gone', 'member', 'not-a-real-hash') RETURNING id"
                ),
                {"c": customer, "e": f"m-{uuid.uuid4().hex}@example.test"},
            ).scalar_one()
        )
        document = insert_document(conn, customer, uploaded_by=admin)
        chat = new_chat(conn, customer, admin)
        scan = add_usage(conn, customer, user_id=admin, document_id=document, cost_usd=0.25)
        asked = add_usage(conn, customer, user_id=admin, conversation_id=chat, kind="chat", cost_usd=0.5)
        by_member = add_usage(conn, customer, user_id=member, kind="search", cost_usd=0.125)
    with db.customer(customer) as conn:  # Delete forever, as the app does it
        delete_forever(conn, MemoryStorage(), customer, document)
    as_owner(db, "DELETE FROM conversations WHERE id = :x", {"x": chat})
    as_owner(db, "DELETE FROM users WHERE id = :u", {"u": member})

    rows = as_owner(
        db,
        "SELECT id, user_id, document_id, conversation_id, cost_usd FROM model_usage"
        " WHERE customer_id = :c ORDER BY id",
        {"c": customer},
    )
    assert [(row[0], row[2], row[3], float(row[4])) for row in rows] == [
        (scan, None, None, 0.25),
        (asked, None, None, 0.5),
        (by_member, None, None, 0.125),
    ]
    assert [None if row[1] is None else str(row[1]) for row in rows] == [admin, admin, None]


def test_an_organisations_purge_still_works_and_takes_its_ledger_and_models(db: Database) -> None:
    """0018's finish is unchanged: its user delete nulls user_id, and the customer's
    delete cascades the ledger and the organisation's models."""
    customer, admin = new_customer(db)
    model = catalog_model(db)
    allow(db, customer, [model], model)
    with db.customer(customer) as conn:
        chat = new_chat(conn, customer, admin)
        add_usage(conn, customer, user_id=admin, conversation_id=chat, kind="chat")
        add_usage(conn, customer, kind="embed")
    as_owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(), ai_budget_usd = 5,"
        " delete_after = now() - interval '1 minute' WHERE id = :c",
        {"c": customer},
    )
    with db.system() as conn:  # the worker's call, as the app
        finished = conn.execute(
            text("SELECT kaagaz_finish_customer_delete(:c)"), {"c": customer}
        ).scalar_one()
    assert finished is True
    counts = as_owner(
        db,
        "SELECT (SELECT count(*) FROM model_usage WHERE customer_id = :c),"
        " (SELECT count(*) FROM customer_models WHERE customer_id = :c),"
        " (SELECT count(*) FROM customers WHERE id = :c)",
        {"c": customer},
    )
    assert counts == [(0, 0, 0)]
    assert as_owner(db, "SELECT count(*) FROM model_catalog WHERE id = :m", {"m": model}) == [(1,)]
