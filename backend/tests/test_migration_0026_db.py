"""Migration 0026 (features 25-27, council 15 Oct 2026): model permissions, Dynamic reading
and Ensemble.

Up, down (0024's schema and comments exactly) and up again; an organisation set up before
0026 reads with the same scan model after it (its rows are scan rows); the downgrade
deletes the chat-only rows first and keeps the scan ones; every new function, index and
policy carries a comment; the operator's daily numbers split the new kinds of work
(careful and ensemble_scan as document work, never as scans; ensemble_chat as questions).
Grants, policies and pinned signatures: test_isolation_schema; the functions' outcomes and
customer separation: test_isolation_ai_settings.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.db import models
from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.settings_factory import make_settings
from tests.test_migration_0004_db import _current, _head
from tests.test_migration_0007_db import _dictionary
from tests.test_migration_0023_db import allow, as_owner, catalog_model
from tests.test_operator_insights_db import at, new_model, of, operator, read, usage, window_day

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
SETTINGS = make_settings()
NEW_TABLES = {"customer_ai_settings", "platform_models", "platform_ai_settings"}
FUNCTIONS = {"kaagaz_op_org_ai", "kaagaz_op_set_org_ai", "kaagaz_op_ai_defaults", "kaagaz_op_set_ai_defaults"}
REPLACED = {"kaagaz_op_org_models", "kaagaz_op_set_org_models"}
NEW_INDEXES = {
    "ai_customer_models_one_default_chat",
    "ai_platform_models_one_default_scan",
    "ai_platform_models_one_default_chat",
}
NEW_COLUMNS = {
    ("platform_audit", "details"),
    ("model_catalog", "chat_eligible"),
    ("model_catalog", "chat_verified_prompt"),
    ("customer_models", "scan"),
    ("customer_models", "chat"),
    ("customer_models", "is_default_chat"),
    ("documents", "read_mode"),
    ("documents", "read_also"),
    ("extracted_values", "votes"),
}


def test_0026_up_down_up_keeps_a_0023_organisations_scan_model(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0024")
    customer, _ = new_customer(database)
    model = catalog_model(database)
    allow(database, customer, [model], model)
    with database.customer(customer) as conn:
        document = insert_document(conn, customer)
    at_0024, comments_0024 = m0002._schema(database), _dictionary(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head, comments_head = m0002._schema(database), _dictionary(database)
    assert m0002._names(at_head, "relations") >= NEW_TABLES | NEW_INDEXES
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    assert not m0002._names(at_head, "functions") & REPLACED
    assert set(comments_head) >= NEW_COLUMNS | {(table, None) for table in NEW_TABLES}
    with database.customer(customer) as conn:
        reading = models.resolve_scan(conn, customer, document, settings=SETTINGS)
        mode = conn.execute(
            text("SELECT read_mode, read_also FROM documents WHERE id = :d"), {"d": document}
        ).one()
    assert (reading.mode, reading.lead.model, tuple(mode)) == ("single", model, ("single", []))
    assert as_owner(
        database, "SELECT model, scan, chat FROM customer_models WHERE customer_id = :c", {"c": customer}
    ) == [(model, True, False)]
    assert as_owner(
        database, "SELECT chat_levels, chat_default_level, dynamic FROM platform_ai_settings"
    ) == [(["low"], "low", False)]  # the seed: today's behaviour

    answer = catalog_model(database)
    as_owner(
        database,
        "INSERT INTO customer_models (customer_id, model, scan, chat) VALUES (:c, :m, false, true)",
        {"c": customer, "m": answer},
    )
    m0002._downgrade(url, "0024")
    assert m0002._schema(database) == at_0024
    assert _dictionary(database) == comments_0024
    assert as_owner(
        database, "SELECT model FROM customer_models WHERE customer_id = :c", {"c": customer}
    ) == [(model,)]  # the chat-only row went first
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
        " WHERE p.polrelid IN ('customer_ai_settings'::regclass, 'platform_models'::regclass,"
        " 'platform_ai_settings'::regclass)",
        {"f": sorted(FUNCTIONS), "i": sorted(NEW_INDEXES)},
    )
    assert len(comments) == len(FUNCTIONS) + len(NEW_INDEXES) + 3  # customer_only and two read_all
    assert [name for name, comment in comments if not (comment or "").strip()] == []


def test_the_operators_daily_numbers_split_the_new_kinds_of_work(db: Database) -> None:
    """D7: careful and ensemble_scan are document work, never scans (the per-page average
    stays on scan); ensemble_chat is a question's."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    reader, chat = new_model(), new_model()
    when = at(day)
    usage(db, customer, when, "scan", reader, 5, "0.5")
    usage(db, customer, when, "careful", reader, 2, "0.4")
    usage(db, customer, when, "ensemble_scan", reader, 3, "0.3")
    usage(db, customer, when, "chat", chat, 1, "0.02")
    usage(db, customer, when, "ensemble_chat", chat, 2, "0.04")

    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), customer)
    assert (daily["scan_pages"], daily["cost_scan_usd"]) == (5, Decimal("0.5"))
    assert daily["cost_documents_usd"] == Decimal("1.2")
    assert daily["cost_questions_usd"] == Decimal("0.06")
    assert daily["cost_usd"] == Decimal("1.26")
