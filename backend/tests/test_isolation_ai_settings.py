"""Customer separation of the organisations' AI settings and answer models, the platform
defaults, and the operator's AI functions (features 25-27, council 15 Oct 2026, C2-C3,
C9-C10, S1-S4, D1, D8; 0026).

Customer A never reads B's settings or answer models, and B's never count for A. The app
only reads the new tables, never writes a model's answer verification, and runs none of
the operator's functions. Those act only for an enabled operator whose two-step secret is
confirmed, need a fresh code for every save, refuse what the platform does not offer (one
test per outcome), keep money out of the organisation's own log, and count the
organisations each platform area reaches. The worker's resolver checks the mode, the
other readers and the vendor again at job time.

Tests that save platform defaults put them back (empty, low, Dynamic off) before they end:
every other test reads them.
"""

import itertools
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.operator_orgs import Outcome
from tests.db_support import insert_document, new_customer, violation
from tests.settings_factory import make_settings
from tests.test_migration_0023_db import PROMPT, as_owner, catalog_model, new_operator

pytestmark = pytest.mark.db

SETTINGS = make_settings()
_STEPS = itertools.count(1_000)  # each new operator has no step yet, so any rising step is fresh
NO_DEFAULTS = ops.AiChoice(chat_levels=["low"], chat_default_level="low", dynamic=False)


def chat_model(db: Database, **columns: Any) -> str:
    """A catalogue model (catalog_model's) also eligible and verified for answering, at
    every level (catalog_model's own accept none)."""
    model = catalog_model(db, **columns)
    as_owner(
        db,
        "UPDATE model_catalog SET chat_eligible = true, chat_verified_prompt = :h, chat_verified_at = now(),"
        " efforts = ARRAY['low', 'medium', 'high'] WHERE id = :m",
        {"h": ANSWER_PROMPT_HASH, "m": model},
    )
    return model


def set_org(
    db: Database,
    operator: str,
    customer: str,
    choice: ops.AiChoice,
    *,
    ensemble: bool = False,
    budget: Decimal | None = None,
    step: int | None = None,
) -> Outcome:
    with OperatorDatabase(db.engine).operator() as conn:
        return ops.set_org_ai(
            conn,
            operator,
            customer,
            choice,
            ensemble=ensemble,
            budget=budget,
            scan_prompt=PROMPT,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=next(_STEPS) if step is None else step,
        )


def set_defaults(db: Database, operator: str, choice: ops.AiChoice, *, step: int | None = None) -> str:
    with OperatorDatabase(db.engine).operator() as conn:
        return ops.set_ai_defaults(
            conn,
            operator,
            choice,
            scan_prompt=PROMPT,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=next(_STEPS) if step is None else step,
        )


def defaults(db: Database, operator: str) -> dict[str, Any]:
    with OperatorDatabase(db.engine).operator() as conn:
        row = ops.ai_defaults(conn, operator)
    assert row is not None
    return dict(row)


@pytest.fixture
def platform(db: Database) -> Iterator[str]:
    """An operator whose platform defaults saves are put back to none when the test ends."""
    operator = new_operator(db)
    try:
        yield operator
    finally:
        assert set_defaults(db, operator, NO_DEFAULTS) == "done"


# --- customer A and customer B -----------------------------------------------------------


def test_a_customer_never_reads_another_customers_ai_settings_or_answer_models(db: Database) -> None:
    operator = new_operator(db)
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    scan, theirs = chat_model(db), chat_model(db)
    choice = ops.AiChoice([scan], scan, [theirs], theirs, "medium", ["low", "medium"], "medium", False)
    assert set_org(db, operator, b, choice) == Outcome("done", b)
    with db.customer(a) as conn:
        settings = conn.execute(text("SELECT CAST(customer_id AS text) FROM customer_ai_settings")).all()
        rows = conn.execute(text("SELECT model FROM customer_models WHERE chat")).all()
        theirs_seen = models.usable_chat_models(conn, b, settings=SETTINGS)
        effective = dict(models.effective_settings(conn, b))
        with pytest.raises(models.AiNotAllowed) as refused:
            models.resolve_chat(conn, a, [theirs], None, settings=SETTINGS)
    assert (settings, rows, theirs_seen) == ([], [], [])
    assert effective == {  # Changed on purpose (#463, 31 Oct 2026): the scan cost settings too
        "scan_thinking": None,
        "chat_levels": ["low"],
        "chat_default_level": "low",
        "scan_resolution": None,
        "careful_thinking": None,
        "careful_resolution": None,
        "page_max_output_tokens": None,
        "dynamic": False,
        "cost_saving": True,  # Changed on purpose (#463 review): whether the switches allow Dynamic
        "ensemble": False,
    }  # the platform's, never B's
    assert refused.value.code == "model_not_allowed"
    with db.customer(b) as conn:
        own = models.resolve_chat(conn, b, None, None, settings=SETTINGS)
    assert ([agent.model for agent in own.agents], own.thinking) == ([theirs], "medium")


# --- what the app may not do -----------------------------------------------------------------

_APP_REFUSED = (
    "INSERT INTO customer_ai_settings (customer_id, ensemble) VALUES (:c, true)",
    "UPDATE customer_ai_settings SET ensemble = true WHERE customer_id = :c",
    "DELETE FROM customer_ai_settings WHERE customer_id = :c",
    "UPDATE customer_models SET chat = true WHERE customer_id = :c",
    "INSERT INTO platform_models (model, scan) VALUES (:m, true)",
    "DELETE FROM platform_models",
    "UPDATE platform_ai_settings SET dynamic = true",
    "UPDATE model_catalog SET chat_verified_prompt = repeat('a', 64), chat_verified_at = now() WHERE id = :m",
    "UPDATE model_catalog SET chat_verified_prompt = NULL, chat_verified_at = NULL WHERE id = :m",
)


@pytest.mark.parametrize("statement", _APP_REFUSED)
def test_the_app_only_reads_the_ai_settings_and_never_verifies_a_model(db: Database, statement: str) -> None:
    customer, _ = new_customer(db)
    model = chat_model(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer, "m": model})
    assert violation(refused.value)[0] == "42501"  # insufficient_privilege
    with db.customer(customer) as conn:  # but it reads them
        conn.execute(text("SELECT * FROM platform_models, platform_ai_settings, customer_ai_settings")).all()


# Changed on purpose (#463, 31 Oct 2026): both saves take the three scan cost settings after the
# scan level (0064), and the estimate's function is the operator's too.
OP_CALLS = (
    "SELECT * FROM kaagaz_op_org_ai(gen_random_uuid(), gen_random_uuid())",
    "SELECT * FROM kaagaz_op_set_org_ai(gen_random_uuid(), gen_random_uuid(), ARRAY['a/b'], 'a/b',"
    " '{}', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, false, NULL, 'g2', NULL, 1)",
    "SELECT * FROM kaagaz_op_ai_defaults(gen_random_uuid())",
    "SELECT kaagaz_op_set_ai_defaults(gen_random_uuid(), '{}', NULL, '{}', NULL, NULL, NULL, NULL, NULL,"
    " NULL, ARRAY['low'], 'low', false, 'g2', NULL, 1)",
    "SELECT * FROM kaagaz_op_scan_cost(gen_random_uuid(), NULL, 'a/b', 'low', 'high', NULL, NULL, NULL,"
    " 'high', 'high')",
    "SELECT kaagaz_op_set_model_enabled(gen_random_uuid(), 'a/b', true, 'g2', NULL, 1)",
)


@pytest.mark.parametrize("call", OP_CALLS)
def test_the_app_runs_none_of_the_operators_ai_functions(db: Database, call: str) -> None:
    customer, _ = new_customer(db)
    for begin in (db.system, lambda: db.customer(customer)):
        with pytest.raises(DBAPIError) as refused, begin() as conn:
            conn.execute(text(call))
        assert violation(refused.value)[0] == "42501", begin


# --- the operator's functions ------------------------------------------------------------


def test_the_ai_functions_act_only_for_an_enabled_confirmed_operator(db: Database) -> None:
    customer, _ = new_customer(db)
    model = catalog_model(db)
    choice = ops.AiChoice([model], model, chat_levels=["low"], chat_default_level="low", dynamic=False)
    for operator in (new_operator(db, disabled=True), new_operator(db, confirmed=False), str(uuid.uuid4())):
        assert set_org(db, operator, customer, choice) == Outcome("signed_out", None)
        assert set_defaults(db, operator, choice) == "signed_out"
        with OperatorDatabase(db.engine).operator() as conn:
            assert (ops.org_ai(conn, operator, customer), ops.ai_defaults(conn, operator)) == (None, None)
    assert as_owner(db, "SELECT count(*) FROM customer_models WHERE customer_id = :c", {"c": customer}) == [
        (0,)
    ]


def _scan_only(model: str) -> ops.AiChoice:
    return ops.AiChoice([model], model)


def _deleting(db: Database, customer: str) -> None:
    as_owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(), delete_after = now() WHERE id = :c",
        {"c": customer},
    )


def _save_for_outcome(db: Database, outcome: str, operator: str, customer: str) -> Outcome:
    """A save that reaches ``outcome`` (fresh catalogue models each time)."""
    if outcome == "signed_out":
        operator = str(uuid.uuid4())
    if outcome == "missing":
        customer = str(uuid.uuid4())
    if outcome == "deleting":
        _deleting(db, customer)
    if outcome == "step_up":
        set_org(db, operator, customer, ops.AiChoice([catalog_model(db)], None), step=500)  # no change
        return set_org(db, operator, customer, _scan_only(catalog_model(db)), step=500)  # the same code
    if outcome == "unavailable":
        choice = _scan_only(catalog_model(db, enabled=False))
    elif outcome == "not_verified":
        choice = _scan_only(catalog_model(db, verified="g0"))
    elif outcome == "chat_not_verified":
        answer = chat_model(db)
        as_owner(
            db, "UPDATE model_catalog SET chat_verified_prompt = repeat('0', 64) WHERE id = :m", {"m": answer}
        )
        choice = ops.AiChoice(chat_models=[answer], chat_default=answer)
    elif outcome == "level_not_supported":
        choice = ops.AiChoice(chat_levels=["low", "huge"], chat_default_level="low")
    elif outcome == "default_not_allowed":
        choice = ops.AiChoice([catalog_model(db)], None)
    else:  # signed_out, missing, deleting and done
        choice = _scan_only(catalog_model(db))
    return set_org(db, operator, customer, choice)


OUTCOMES = (
    "signed_out",
    "step_up",
    "missing",
    "deleting",
    "unavailable",
    "not_verified",
    "chat_not_verified",
    "level_not_supported",
    "default_not_allowed",
    # Changed on purpose (Tushar, 16 Oct 2026: any number of models): 0037 returns neither
    # needs_two_models_scan nor needs_two_models_chat.
    "done",
)


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_each_outcome_of_an_organisations_ai_save(db: Database, outcome: str) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    result = _save_for_outcome(db, outcome, operator, customer)
    assert result.code == outcome
    changed = as_owner(db, "SELECT count(*) FROM customer_models WHERE customer_id = :c", {"c": customer})
    assert changed == [(1 if outcome == "done" else 0,)]  # only done changes anything


def test_a_level_a_model_does_not_accept_is_refused_for_its_area(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    low_only = chat_model(db)
    as_owner(db, "UPDATE model_catalog SET efforts = ARRAY['low'] WHERE id = :m", {"m": low_only})
    medium = ops.AiChoice(
        chat_models=[low_only], chat_default=low_only, chat_levels=["medium"], chat_default_level="medium"
    )
    assert set_org(db, operator, customer, medium).code == "level_not_supported"
    thinking = ops.AiChoice([low_only], low_only, scan_thinking="high")
    assert set_org(db, operator, customer, thinking).code == "level_not_supported"


def test_a_model_with_no_thinking_levels_takes_any_level_for_an_organisation_and_the_platform(
    db: Database, platform: str
) -> None:
    # 0048 (Tushar, 18 Oct 2026): a model that lists no levels has one mode and takes any level.
    customer, _ = new_customer(db)
    scan, answer = catalog_model(db), chat_model(db)  # catalog_model's accept no levels
    as_owner(db, "UPDATE model_catalog SET efforts = '{}' WHERE id = :m", {"m": answer})
    choice = ops.AiChoice([scan], scan, [answer], answer, "high", ["low", "high"], "high", False)
    assert set_org(db, platform, customer, choice).code == "done"
    assert set_defaults(db, platform, choice) == "done"
    # A model whose levels leave out the one asked for is still refused, in each area.
    low_only = chat_model(db)
    as_owner(db, "UPDATE model_catalog SET efforts = ARRAY['low'] WHERE id = :m", {"m": low_only})
    for refused in (
        ops.AiChoice([low_only], low_only, [answer], answer, "high", ["low"], "low", False),
        ops.AiChoice([scan], scan, [low_only], low_only, "low", ["low", "high"], "high", False),
    ):
        assert set_org(db, platform, customer, refused).code == "level_not_supported"
        assert set_defaults(db, platform, refused) == "level_not_supported"


def test_both_saves_need_a_fresh_code(db: Database, platform: str) -> None:
    customer, _ = new_customer(db)
    model = catalog_model(db)
    choice = ops.AiChoice([model], model)
    assert set_org(db, platform, customer, choice, step=7).code == "done"
    assert set_org(db, platform, customer, choice, step=7).code == "step_up"  # the same code
    assert set_defaults(db, platform, NO_DEFAULTS, step=6) == "step_up"  # an older code
    with OperatorDatabase(db.engine).operator() as conn:  # no code at all
        no_code = str(
            conn.execute(
                text(
                    "SELECT kaagaz_op_set_ai_defaults(:o, '{}', NULL, '{}', NULL, NULL, NULL, NULL, NULL,"
                    " NULL, ARRAY['low'], 'low', false, :p, :h, NULL)"
                ),
                {"o": platform, "p": PROMPT, "h": ANSWER_PROMPT_HASH},
            ).scalar_one()
        )
    assert no_code == "step_up"


def test_the_organisations_own_log_holds_no_budget_and_the_operators_does(db: Database) -> None:
    """D1: money stays with the operator."""
    operator = new_operator(db)
    customer, _ = new_customer(db)
    scan, answer = catalog_model(db), chat_model(db)
    choice = ops.AiChoice([scan], scan, [answer], answer, None, ["low"], "low", None)
    assert set_org(db, operator, customer, choice, budget=Decimal("12.5")).code == "done"
    [(details,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert details == {
        "by": "operator",
        "scan_models": [scan],
        "scan_default": scan,
        "chat_models": [answer],
        "chat_default": answer,
        # Changed on purpose (#463 review, X1): no scan cost setting in the organisation's own log.
        "chat_levels": ["low"],
        "chat_default_level": "low",
        "dynamic": None,
        "ensemble": False,
    }
    [(platform_details,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    )
    hidden = {
        "scan_thinking": None,
        "scan_resolution": None,
        "careful_thinking": None,
        "careful_resolution": None,
        "page_max_output_tokens": None,
    }  # the scan cost settings: the operator's log only
    assert platform_details == {**details, **hidden, "budget_usd": 12.5}
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, operator, customer)
    assert own is not None and own["ai_budget_usd"] == Decimal("12.50")


def test_the_platform_defaults_count_the_organisations_each_area_reaches(db: Database, platform: str) -> None:
    """D8: an organisation inherits an area while it has none of its own."""
    before = defaults(db, platform)
    customer, _ = new_customer(db)
    after = defaults(db, platform)
    areas = ("scan_models", "chat_models", "scan_thinking", "chat_levels", "dynamic")
    assert [after[f"orgs_inheriting_{area}"] - before[f"orgs_inheriting_{area}"] for area in areas] == [1] * 5
    model = chat_model(db)  # accepts every level
    assert set_org(db, platform, customer, ops.AiChoice([model], model, scan_thinking="low")).code == "done"
    own = defaults(db, platform)
    assert [own[f"orgs_inheriting_{area}"] - before[f"orgs_inheriting_{area}"] for area in areas] == [
        0,
        1,
        0,
        1,
        1,
    ]

    a, b = catalog_model(db), catalog_model(db)
    assert (
        set_defaults(
            db, platform, ops.AiChoice([a, b], b, chat_levels=["low"], chat_default_level="low", dynamic=True)
        )
        == "done"
    )
    [(details,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert details["after"]["scan_models"] == sorted([a, b]) and details["after"]["dynamic"] is True
    changed = details["orgs_changed"]
    assert (changed["scan_models"], changed["dynamic"]) == (
        own["orgs_inheriting_scan_models"],
        own["orgs_inheriting_dynamic"],
    )
    assert (changed["chat_models"], changed["scan_thinking"], changed["chat_levels"]) == (0, 0, 0)
    assert (
        set_defaults(
            db, platform, ops.AiChoice([a], a, chat_levels=["low"], chat_default_level="low", dynamic=True)
        )
        == "done"  # Changed on purpose (Tushar, 16 Oct 2026: any number of models): Cost saving with one
    )


def test_a_model_the_platform_defaults_name_cannot_be_disabled(db: Database, platform: str) -> None:
    model = catalog_model(db)
    assert (
        set_defaults(
            db,
            platform,
            ops.AiChoice([model], model, chat_levels=["low"], chat_default_level="low", dynamic=False),
        )
        == "done"
    )
    with OperatorDatabase(db.engine).operator() as conn:
        outcome = ops.set_model_enabled(
            conn,
            platform,
            model,
            enabled=False,
            scan_prompt=PROMPT,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=next(_STEPS),
        )
    assert outcome == "in_use"


def test_an_organisations_settings_go_with_it(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    choice = ops.AiChoice(chat_levels=["low", "medium"], chat_default_level="low", dynamic=False)
    assert set_org(db, operator, customer, choice).code == "done"
    as_owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(),"
        " delete_after = now() - interval '1 minute' WHERE id = :c",
        {"c": customer},
    )
    with db.system() as conn:
        assert conn.execute(text("SELECT kaagaz_finish_customer_delete(:c)"), {"c": customer}).scalar_one()
    assert as_owner(
        db, "SELECT count(*) FROM customer_ai_settings WHERE customer_id = :c", {"c": customer}
    ) == [(0,)]


# --- the worker's own check at job time (S2, S1) -----------------------------------------------


def _document(db: Database, customer: str, **columns: Any) -> str:
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        conn.execute(
            text(
                "UPDATE documents SET read_model = :m, read_mode = :mode, read_also = CAST(:also AS text[])"
                " WHERE id = :d"
            ),
            {
                "m": columns.get("read_model"),
                "mode": columns.get("read_mode", "single"),
                "also": columns.get("read_also", []),
                "d": document,
            },
        )
    return document


def _scan(db: Database, customer: str, document: str, settings: Any = SETTINGS) -> models.Reading:
    with db.customer(customer) as conn:
        return models.resolve_scan(conn, customer, document, settings=settings)


def test_a_mode_revoked_after_upload_reads_single_and_a_junk_reader_is_never_used(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    lead, other, outsider = catalog_model(db), catalog_model(db), catalog_model(db)
    assert set_org(db, operator, customer, ops.AiChoice([lead, other], lead), ensemble=True).code == "done"
    document = _document(db, customer, read_model=lead, read_mode="ensemble", read_also=[other, outsider])
    reading = _scan(db, customer, document)
    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): an old Ensemble document is
    # read by its lead alone, so neither other nor outsider is ever read.
    # Changed on purpose (18 Oct 2026 rule; 0048): catalog_model's lead lists no thinking
    # levels, so it reads with none and says so (no_thinking_levels).
    assert (reading.mode, reading.lead.model, reading.others, reading.notes) == (
        "single",
        lead,
        (),
        ("no_thinking_levels", "ensemble_after_scan"),
    )
    assert set_org(db, operator, customer, ops.AiChoice([lead, other], lead), ensemble=False).code == "done"
    revoked = _scan(db, customer, document)
    assert (revoked.mode, revoked.others, revoked.notes) == (
        "single",
        (),
        ("no_thinking_levels", "ensemble_after_scan"),
    )


def test_one_scanning_model_saves_with_cost_saving_and_ensemble_saved_off(db: Database) -> None:
    # 0037 (Tushar, 16 Oct 2026: any number of models): Cost saving re-reads with the lead
    # itself; Ensemble, which needs a second scanning model, is saved off, not refused.
    operator = new_operator(db)
    customer, _ = new_customer(db)
    lead = catalog_model(db)
    choice = ops.AiChoice([lead], lead, dynamic=True)
    assert set_org(db, operator, customer, choice, ensemble=True).code == "done"
    with db.customer(customer) as conn:
        allowed = models.effective_settings(conn, customer)
    assert (allowed["dynamic"], allowed["ensemble"]) == (True, False)
    [(details,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert (details["dynamic"], details["ensemble"]) == (True, False)
    reading = _scan(db, customer, _document(db, customer, read_model=lead, read_mode="dynamic"))
    assert (reading.mode, reading.lead.model, reading.careful) == ("dynamic", lead, reading.lead)
    # Two scanning models and one answering model: Ensemble stays on (Ask answers with one).
    answer, other = chat_model(db), catalog_model(db)
    two = ops.AiChoice([lead, other], lead, [answer], answer)
    assert set_org(db, operator, customer, two, ensemble=True).code == "done"
    with db.customer(customer) as conn:
        assert models.effective_settings(conn, customer)["ensemble"] is True


def test_a_vendor_outside_the_allow_list_is_unusable_even_when_enabled_and_verified(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    model = catalog_model(db)  # a google model
    assert set_org(db, operator, customer, ops.AiChoice([model], model)).code == "done"
    document = _document(db, customer)
    assert _scan(db, customer, document).lead.model == model
    anthropic_only = make_settings(openrouter_model_vendors=frozenset({"anthropic"}))
    assert _scan(db, customer, document, anthropic_only).lead.model == SETTINGS.ocr_model


def test_a_scan_level_the_lead_does_not_accept_falls_back_to_the_environments(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    model = catalog_model(db)
    as_owner(db, "UPDATE model_catalog SET efforts = ARRAY['low', 'high'] WHERE id = :m", {"m": model})
    assert set_org(db, operator, customer, ops.AiChoice([model], model, scan_thinking="high")).code == "done"
    reading = _scan(db, customer, _document(db, customer))
    assert (reading.thinking, reading.notes) == ("high", ())
    as_owner(db, "UPDATE model_catalog SET efforts = ARRAY['low'] WHERE id = :m", {"m": model})
    reading = _scan(db, customer, _document(db, customer))
    assert (reading.thinking, reading.notes) == ("low", ("thinking_fallback",))


def test_a_bad_reading_choice_is_refused_by_the_table(db: Database) -> None:
    customer, _ = new_customer(db)
    model = catalog_model(db)
    for columns, constraint in (
        ({"read_mode": "automatic"}, "ai_documents_read_mode"),
        ({"read_mode": "single", "read_also": [model]}, "ai_documents_read_mode_models"),
        (
            {"read_mode": "ensemble", "read_model": model, "read_also": [model]},
            "ai_documents_read_mode_models",
        ),
        (
            {"read_mode": "ensemble", "read_model": model, "read_also": ["Not a model"]},
            "ai_documents_read_also_shape",
        ),
    ):
        with pytest.raises(DBAPIError) as refused:
            _document(db, customer, **columns)
        assert violation(refused.value) == ("23514", constraint), columns
