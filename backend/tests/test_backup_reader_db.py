"""The backup reader with the database (#664, Tushar, 18 Nov 2026; 0101): who reads once a page
Mistral read no text on, saved per organisation and as the platform default next to the reader
choice, off until the super admin sets it.

Migration 0101 adds ``backup_reader`` and ``backup_model`` to both AI settings tables, NULL for every
row (nothing reads differently on release day); the operator's four AI functions save, check, audit
(the operator's log only) and count it; a model named as a backup reader can't be disabled. The
worker resolves it at job time (``models.backup_reader``), for a document on Mistral only, and
never trusts a model that is no longer usable. The operator's API checks the model as it checks
the reader choice, before a code is used. Every test that saves the platform defaults puts them
back (the ``platform`` fixture)."""

import logging
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import totp
from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.reading.ocr import READER_PROMPT_VERSION, BackupReader, UlcCeiling, UlcPrices
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer, violation
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api
from tests.test_isolation_ai_settings import NO_DEFAULTS, platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _enable, _save, _signed_in
from tests.test_reader_choice_db import _job_reading
from tests.test_scan_cost_settings_db import SETTINGS, scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
fresh = base.fresh
M0101 = _load("0101_backup_reader.py")
_TABLES = ("customer_ai_settings", "platform_ai_settings")
ORG_BODY = {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
PLATFORM_BODY = {**FOLLOWS_DEFAULTS, "chat_levels": ["low"], "chat_default_level": "low", "dynamic": False}
FUNCTIONS = (
    "kaagaz_op_org_ai",
    "kaagaz_op_ai_defaults",
    "kaagaz_op_set_org_ai",
    "kaagaz_op_set_ai_defaults",
    "kaagaz_op_set_model_enabled",
)
_BODIES = text(
    "SELECT p.proname, pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner),"
    " obj_description(p.oid, 'pg_proc'), coalesce(p.proacl::text, '')"
    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
    " WHERE n.nspname = 'public' AND p.proname = ANY (:names) ORDER BY 1, 2"
)
MISTRAL = ops.AiChoice(scan_reader="mistral")


def _bodies(db: Database) -> list[tuple[str, ...]]:
    with db.system() as conn:
        return [tuple(row) for row in conn.execute(_BODIES, {"names": list(FUNCTIONS)})]


def _refused(db: Database, statement: str, params: dict[str, object]) -> str | None:
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, statement, params)
    return violation(refused.value)[0]


def _resolved(db: Database, customer: str, settings: Any = SETTINGS) -> BackupReader | None:
    with db.customer(customer) as conn:
        scan = models.resolve_scan(conn, customer, insert_document(conn, customer), settings=settings)
        return models.backup_reader(conn, customer, scan, settings=settings)


# --- the migration ----------------------------------------------------------------------------


def test_the_revision_follows_0100() -> None:
    assert (M0101.revision, M0101.down_revision) == ("0101", "0100")


def test_upgrade_leaves_every_backup_reader_off_and_changes_no_reader(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0100")
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_reader) VALUES (:c, 'mistral')",
        {"c": customer},
    )
    migrate(url, "0101")
    assert as_owner(db, "SELECT scan_reader, backup_reader, backup_model FROM customer_ai_settings") == [
        ("mistral", None, None)
    ]
    assert as_owner(db, "SELECT backup_reader, backup_model FROM platform_ai_settings") == [(None, None)]
    columns = {row[:2]: row[2:4] for row in base._schema(db)["columns"] if row[0] in _TABLES}
    for table in _TABLES:
        assert columns[(table, "backup_reader")] == columns[(table, "backup_model")] == ("text", False)
        comments = as_owner(
            db,
            "SELECT attname, col_description(attrelid, attnum) FROM pg_attribute"
            " WHERE attrelid = CAST(:t AS regclass) AND attname IN ('backup_reader', 'backup_model')",
            {"t": table},
        )
        assert len(comments) == 2 and all(comment for _name, comment in comments)
        # The app and the worker read them alike, and neither writes them.
        for role in ("kaagaz_app", "kaagaz_worker"):
            for column in ("backup_reader", "backup_model"):
                [(read, write)] = as_owner(
                    db,
                    "SELECT has_column_privilege(:r, CAST(:t AS regclass), :c, 'SELECT'),"
                    " has_column_privilege(:r, CAST(:t AS regclass), :c, 'UPDATE')",
                    {"r": role, "t": table, "c": column},
                )
                assert (read, write) == (True, False), (role, table, column)


def test_the_checks_take_a_whole_pair_naming_a_catalogue_model(db: Database) -> None:
    customer, _ = new_customer(db)
    model = scan_model(db)
    insert = "INSERT INTO customer_ai_settings (customer_id, backup_reader, backup_model) VALUES (:c, :r, :m)"
    for reader, chosen, code in (
        ("gemini", None, "23514"),  # not a way to read
        ("openrouter", None, "23514"),  # a way to read with no model
        ("ultra_low_cost", None, "23514"),
        ("off", model, "23514"),  # a model while off
        (None, model, "23514"),  # a model with nothing named
        ("openrouter", "google/not-in-the-catalogue", "23503"),  # the catalogue's ids only
    ):
        assert _refused(db, insert, {"c": customer, "r": reader, "m": chosen}) == code, (reader, chosen)
    for reader, chosen in (("openrouter", model), ("ultra_low_cost", model), ("off", None), (None, None)):
        as_owner(db, insert, {"c": customer, "r": reader, "m": chosen})
        as_owner(db, "DELETE FROM customer_ai_settings WHERE customer_id = :c", {"c": customer})
    assert _refused(db, "UPDATE platform_ai_settings SET backup_reader = 'openrouter'", {}) == "23514"
    names = {row[1] for row in base._schema(db)["constraints"] if row[0] in _TABLES}
    for side in ("customer", "platform"):
        assert {f"ai_{side}_settings_backup_reader", f"ai_{side}_settings_backup_model"} <= names


def test_head_down_to_0100_and_up_again_restores_every_function_exactly(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0100")
    at_0100, bodies_0100 = base._schema(db), _bodies(db)
    migrate(url, "0101")
    at_0101, bodies_0101 = base._schema(db), _bodies(db)
    for name, body, *_rest in bodies_0101:  # the house pattern: definer, empty path
        assert "SECURITY DEFINER" in body and "SET search_path TO ''" in body, name
    [(old_enable, *old_rest)] = [row[1:] for row in bodies_0100 if row[0] == "kaagaz_op_set_model_enabled"]
    [(new_enable, *new_rest)] = [row[1:] for row in bodies_0101 if row[0] == "kaagaz_op_set_model_enabled"]
    assert new_enable == old_enable.replace(*M0101.IN_USE) and new_rest == old_rest  # only its in-use check
    model = scan_model(db)
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, backup_reader, backup_model)"
        " VALUES (:c, 'openrouter', :m)",
        {"c": customer, "m": model},
    )
    as_owner(db, "UPDATE platform_ai_settings SET backup_reader = 'off'")
    base._downgrade(url, "0100")
    assert base._schema(db) == at_0100
    assert _bodies(db) == bodies_0100  # comments, owners and grants too
    migrate(url, "0101")
    assert base._schema(db) == at_0101
    assert _bodies(db) == bodies_0101


# --- the operator's functions -------------------------------------------------------------------


def test_the_org_save_keeps_the_pair_audits_it_for_the_operator_only_and_checks_the_model(
    db: Database, platform: str
) -> None:
    customer, _ = new_customer(db)
    good, unverified, disabled = scan_model(db), scan_model(db), scan_model(db)
    as_owner(db, "UPDATE model_catalog SET verified_prompt_version = 'old' WHERE id = :m", {"m": unverified})
    as_owner(
        db, "UPDATE model_catalog SET enabled_at = NULL, enabled_by = NULL WHERE id = :m", {"m": disabled}
    )
    for reader, chosen, outcome in (
        ("openrouter", None, "backup_reader_incomplete"),
        ("off", good, "backup_reader_incomplete"),
        (None, good, "backup_reader_incomplete"),
        ("openrouter", "google/not-in-the-catalogue", "unavailable"),
        ("openrouter", disabled, "unavailable"),
        ("ultra_low_cost", unverified, "not_verified"),
    ):
        choice = ops.AiChoice(scan_reader="mistral", backup_reader=reader, backup_model=chosen)
        assert set_org(db, platform, customer, choice).code == outcome, (reader, chosen)
    with OperatorDatabase(db.engine).operator() as conn:
        unsaved = ops.org_ai(conn, platform, customer)
    assert unsaved is not None and unsaved["backup_reader"] is None  # nothing saved
    chosen_pair = ops.AiChoice(scan_reader="mistral", backup_reader="openrouter", backup_model=good)
    assert set_org(db, platform, customer, chosen_pair).code == "done"
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, platform, customer)
    assert own is not None and (own["backup_reader"], own["backup_model"]) == ("openrouter", good)
    [(operators,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    )
    assert (operators["backup_reader"], operators["backup_model"]) == ("openrouter", good)
    [(own_log,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert not {"backup_reader", "backup_model"} & set(own_log) and "scan_models" in own_log


def test_the_defaults_save_audits_before_and_after_and_counts_the_organisations_inheriting(
    db: Database, platform: str
) -> None:
    new_customer(db)  # inherits every field
    model = scan_model(db)
    with OperatorDatabase(db.engine).operator() as conn:
        before = ops.ai_defaults(conn, platform)
    assert before is not None and before["orgs_inheriting_backup_reader"] >= 1
    assert (before["backup_reader"], before["backup_model"]) == (None, None)
    incomplete = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, backup_model=model
    )
    assert set_defaults(db, platform, incomplete) == "backup_reader_incomplete"
    chosen = ops.AiChoice(
        chat_levels=["low"],
        chat_default_level="low",
        dynamic=False,
        backup_reader="openrouter",
        backup_model=model,
    )
    assert set_defaults(db, platform, chosen) == "done"
    [(saved,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert (saved["before"]["backup_reader"], saved["after"]["backup_reader"]) == (None, "openrouter")
    assert (saved["before"]["backup_model"], saved["after"]["backup_model"]) == (None, model)
    assert saved["orgs_changed"]["backup_reader"] >= 1
    with OperatorDatabase(db.engine).operator() as conn:
        defaults = ops.ai_defaults(conn, platform)
    assert defaults is not None and (defaults["backup_reader"], defaults["backup_model"]) == (
        "openrouter",
        model,
    )


def test_a_model_named_as_a_backup_reader_cant_be_disabled(db: Database, platform: str) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    for scope in ("org", "platform"):
        model = scan_model(db)
        if scope == "org":
            customer = new_customer(db)[0]
            choice = ops.AiChoice(scan_reader="mistral", backup_reader="ultra_low_cost", backup_model=model)
            assert set_org(db, platform, customer, choice).code == "done"
        else:
            choice = ops.AiChoice(
                chat_levels=["low"],
                chat_default_level="low",
                dynamic=False,
                backup_reader="openrouter",
                backup_model=model,
            )
            assert set_defaults(db, platform, choice) == "done"
        refused = _enable(api, operator, client, model, enabled=False)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "in_use"), scope
        if scope == "org":
            assert set_org(db, platform, customer, MISTRAL).code == "done"
        else:
            assert set_defaults(db, platform, NO_DEFAULTS) == "done"
        assert _enable(api, operator, client, model, enabled=False).status_code == 200, scope


# --- who reads at job time ----------------------------------------------------------------------


def test_it_is_off_until_set_and_only_for_a_document_on_mistral(db: Database, platform: str) -> None:
    model = scan_model(db)
    customer = new_customer(db)[0]
    assert set_org(db, platform, customer, MISTRAL).code == "done"
    assert _resolved(db, customer) is None  # off: nothing saved anywhere
    chosen = ops.AiChoice(scan_reader="mistral", backup_reader="openrouter", backup_model=model)
    assert set_org(db, platform, customer, chosen).code == "done"
    assert _resolved(db, customer) == BackupReader(
        model, "google-vertex", "low", levels=frozenset(("minimal", "low", "medium", "high"))
    )
    gemini = ops.AiChoice(scan_reader="openrouter", backup_reader="openrouter", backup_model=model)
    assert set_org(db, platform, customer, gemini).code == "done"
    assert _resolved(db, customer) is None  # Gemini reads its pages: never a backup reader


def test_an_organisation_inherits_the_platforms_and_its_off_is_off(db: Database, platform: str) -> None:
    model = scan_model(db)
    follows, off = new_customer(db)[0], new_customer(db)[0]
    assert set_org(db, platform, follows, MISTRAL).code == "done"
    turned_off = ops.AiChoice(scan_reader="mistral", backup_reader="off")
    assert set_org(db, platform, off, turned_off).code == "done"
    chosen = ops.AiChoice(
        chat_levels=["low"],
        chat_default_level="low",
        dynamic=False,
        scan_thinking="medium",
        backup_reader="openrouter",
        backup_model=model,
    )
    assert set_defaults(db, platform, chosen) == "done"
    inherited = _resolved(db, follows)
    assert inherited is not None and (inherited.model, inherited.thinking) == (model, "medium")
    assert _resolved(db, off) is None


def test_ultra_low_cost_reads_minimal_at_the_ceiling_with_the_models_prices(
    db: Database, platform: str
) -> None:
    model = scan_model(db)
    as_owner(db, "UPDATE model_catalog SET price_image_usd = 0.001 WHERE id = :m", {"m": model})
    customer = new_customer(db)[0]
    chosen = ops.AiChoice(
        scan_reader="mistral", backup_reader="ultra_low_cost", backup_model=model, ulc_ceiling_paise=30
    )
    assert set_org(db, platform, customer, chosen).code == "done"
    backup = _resolved(db, customer)
    assert backup is not None and (backup.model, backup.thinking) == (model, "minimal")
    assert backup.ulc == UlcCeiling(usd=30 / 100 / 88, paise=30)
    assert backup.ulc_prices == UlcPrices(2.0, 12.0, 1000, 0.001)


def test_a_model_no_longer_usable_is_never_used_and_says_so(
    db: Database, platform: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kaagaz.db.models")
    model, no_minimal = scan_model(db), scan_model(db, ("low", "high"))
    customer, other = new_customer(db)[0], new_customer(db)[0]
    chosen = ops.AiChoice(scan_reader="mistral", backup_reader="openrouter", backup_model=model)
    assert set_org(db, platform, customer, chosen).code == "done"
    as_owner(db, "UPDATE model_catalog SET verified_prompt_version = 'old' WHERE id = :m", {"m": model})
    assert _resolved(db, customer) is None  # no longer verified for the reader's prompt
    as_owner(
        db,
        "UPDATE model_catalog SET verified_prompt_version = :p WHERE id = :m",
        {"p": READER_PROMPT_VERSION, "m": model},
    )
    no_provider = make_settings(openrouter_providers=frozenset({"groq"}))
    assert _resolved(db, customer, no_provider) is None  # no allowed provider
    ulc = ops.AiChoice(scan_reader="mistral", backup_reader="ultra_low_cost", backup_model=no_minimal)
    assert set_org(db, platform, other, ulc).code == "done"  # the API refuses it (409); the worker too
    assert _resolved(db, other) is None
    codes = [getattr(record, "code", None) for record in caplog.records]
    assert codes.count("backup_reader_unusable") == 3


def test_the_worker_passes_it_on_with_the_job(db: Database, platform: str) -> None:
    model = scan_model(db)
    customer = new_customer(db)[0]
    chosen = ops.AiChoice(scan_reader="mistral", backup_reader="openrouter", backup_model=model)
    assert set_org(db, platform, customer, chosen).code == "done"
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    job = _job_reading(db, customer, document)
    assert job.backup is not None and job.backup.model == model
    assert job.reading is not None and job.reading.scan_reader == "mistral"
    assert set_org(db, platform, customer, MISTRAL).code == "done"
    assert _job_reading(db, customer, document).backup is None


# --- the operator's API ---------------------------------------------------------------------------


def test_the_api_shows_the_pair_and_how_many_follow_the_platform(db: Database, platform: str) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    model = scan_model(db)
    customer = api.customer("Backup Reader Shown")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    got = client.get(path).json()
    assert (got["own"]["backup_reader"], got["own"]["backup_model"]) == (None, None)
    body = {**ORG_BODY, "backup_reader": "openrouter", "backup_model": model}  # kept while Gemini reads
    saved = _save(api, operator, client, path, body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["own"]["backup_reader"], saved.json()["own"]["backup_model"]) == (
        "openrouter",
        model,
    )
    defaults = client.get("/api/operator/ai-defaults").json()
    assert (defaults["defaults"]["backup_reader"], defaults["defaults"]["backup_model"]) == (None, None)
    assert defaults["orgs_inheriting"]["backup_reader"] >= 1
    platform_body = {**PLATFORM_BODY, "backup_reader": "off"}
    assert _save(api, operator, client, "/api/operator/ai-defaults", platform_body).status_code == 200
    assert client.get("/api/operator/ai-defaults").json()["defaults"]["backup_reader"] == "off"


def test_the_api_checks_the_model_as_it_checks_the_reader_choice(db: Database, platform: str) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    good, no_minimal, unverified = scan_model(db), scan_model(db, ("low", "high")), scan_model(db)
    as_owner(db, "UPDATE model_catalog SET verified_prompt_version = 'old' WHERE id = :m", {"m": unverified})
    customer = api.customer("Backup Reader Checked")[0]
    for path, body in (
        (f"/api/operator/orgs/{customer}/ai", ORG_BODY),
        ("/api/operator/ai-defaults", PLATFORM_BODY),
    ):
        for pair, status, code in (
            ({"backup_reader": "openrouter", "backup_model": None}, 400, "backup_reader_incomplete"),
            ({"backup_reader": "off", "backup_model": good}, 400, "backup_reader_incomplete"),
            ({"backup_reader": None, "backup_model": good}, 400, "backup_reader_incomplete"),
            ({"backup_reader": "openrouter", "backup_model": "not a model"}, 400, "bad_request"),
            (
                {"backup_reader": "mistral", "backup_model": good},
                400,
                "bad_request",
            ),  # not a way to read again
            (
                {"backup_reader": "openrouter", "backup_model": "google/not-in-the-catalogue"},
                409,
                "unavailable",
            ),
            ({"backup_reader": "openrouter", "backup_model": unverified}, 409, "not_verified"),
            ({"backup_reader": "ultra_low_cost", "backup_model": no_minimal}, 409, "ulc_needs_gemini"),
        ):
            reply = _save(api, operator, client, path, {**body, **pair})
            assert (reply.status_code, reply.json()["error"]["code"]) == (status, code), (
                path,
                pair,
                reply.text,
            )
        for pair in (
            {"backup_reader": "openrouter", "backup_model": good},
            {"backup_reader": "ultra_low_cost", "backup_model": good},
            {"backup_reader": "off", "backup_model": None},
        ):
            reply = _save(api, operator, client, path, {**body, **pair})
            assert reply.status_code == 200, (path, pair, reply.text)


def test_a_check_refused_before_a_code_never_uses_it(db: Database, platform: str) -> None:
    """A pair that is not whole is refused before the code is checked, so the same code still works."""
    api = Api(db)
    operator, client = _signed_in(api)
    customer = api.customer("Backup Reader Code")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    api.clock.advance(totp.STEP_SECONDS)
    code = api.code(operator.secret)
    incomplete = {**ORG_BODY, "backup_reader": "openrouter", "backup_model": None, "code": code}
    assert client.put(path, json=incomplete, headers=CSRF).status_code == 400
    whole = {**ORG_BODY, "backup_reader": "off", "backup_model": None, "code": code}
    assert client.put(path, json=whole, headers=CSRF).status_code == 200
