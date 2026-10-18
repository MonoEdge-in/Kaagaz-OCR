"""The platform operator's models, AI settings and AI spend (model choice, council 11 Oct
2026, MC2, MC3, MS1, MS2, SP3, SP4; features 25-27, council 15 Oct 2026, C10). Every route
needs the operator cookie (``current_operator`` on the router); only ``OperatorDatabase``
here, and each call is one ``kaagaz_op_*`` function (``kaagaz.db.operator_models``), which
writes its own audit rows.

* ``GET /operator/models``: the catalogue, from OpenRouter's live lists (``jobs/catalog``),
  with ``verified`` for the reader's current prompt (``READER_PROMPT_VERSION``),
  ``chat_verified`` for the answer prompt (``ANSWER_PROMPT_HASH``) and ``vendor_allowed``
  (its maker is in OPENROUTER_MODEL_VENDORS; outside it, it is never used), and the real
  cost of each model's scanned pages and answered questions over the last 30 days.
* ``PUT /operator/models/{model}`` {enabled, code} (step-up, as 0016's): enable or disable
  one model on the platform. Only an eligible model verified for scanning or answering
  can be enabled; one an organisation or the platform defaults still name can't be
  disabled.
* ``GET`` and ``PUT /operator/ai-defaults`` (PUT with a fresh code): the platform defaults
  every organisation that has not been customised follows, per area, and how many
  organisations follow each area now.
* ``GET`` and ``PUT /operator/orgs/{id}/ai`` (PUT with a fresh code): one organisation's
  own AI settings (an empty list or a null field follows the platform defaults), its
  Ensemble switch and its monthly budget in dollars (warn only; the operator's alone,
  never in the organisation's own log or screens). Any number of scanning models (0037):
  Ensemble asked with fewer than two in effect is saved off and the PUT says
  ``ensemble_off``.
* ``GET /operator/usage?from&to``: tokens and dollars per organisation and model, days in
  TIMEZONE with both ends included (this month so far when left out), at most
  ``MAX_DAYS``. Never a user or a document: the function has no column for either.

* ``GET /operator/feature-switches``: every product feature, on or off for the whole
  product, and how many organisations use it now. ``PUT .../{feature}`` {enabled, code}
  (step-up): flip it for every organisation; the list comes back (0038).
* ``GET /operator/orgs/{id}/feature-switches``: one organisation's switches, each
  ``{feature, enabled, platform_enabled}`` (no self sign-up). ``PUT .../{feature}``
  {enabled, code} (step-up): flip it for that organisation only; the list comes back. A
  feature runs there only while both switches are on (0039).
* ``GET``, ``POST`` {scan_model, chat_model, note, code} and ``DELETE`` {code}
  ``/operator/test-model`` (POST and DELETE with a fresh code): the platform test model, one
  allowed model for every organisation's scanning and answering, and its undo (every
  organisation back as it was), with what ran on it since it started (pages, questions, the
  cost OpenRouter reported, reading seconds a page). A model must be enabled, eligible and
  verified for its use, and its maker and one provider inside the server's lists (0038).

A refused change is said in plain words; an unknown organisation or model gets 404.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.api.auth import CurrentOperator, OperatorSession, current_operator
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.routers.documents import day_start
from kaagaz.api.routers.operator_orgs import StepUp, StepUpBody, _check, _step_up
from kaagaz.api.views import iso
from kaagaz.db import operator_models as models_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.models import MAX_AGENTS, vendor
from kaagaz.db.operator_models import AiChoice
from kaagaz.db.operator_orgs import Outcome
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.settings import Settings, valid_model_id

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

MAX_DAYS = 366
MAX_ORG_MODELS = 64
# numeric(12,2): the largest budget the column holds.
MAX_BUDGET = Decimal("9999999999.99")
# The outcomes kaagaz.db.operator_orgs' _check does not know, and their replies (C10).
_REFUSALS = {
    "not_eligible": (409, "not_eligible", "This model does not pass the catalogue's rules now."),
    "not_verified": (
        409,
        "not_verified",
        "A model has not passed verify-models for the current prompt. Run it with --record first.",
    ),
    "chat_not_verified": (
        409,
        "chat_not_verified",
        "An answering model has not passed verify-models for the current answer prompt."
        " Run it with --record first.",
    ),
    "in_use": (
        409,
        "in_use",
        "An organisation or the platform defaults still use this model. Remove it there first.",
    ),
    "unavailable": (
        409,
        "unavailable",
        "One of these models is not enabled on the platform now, or can't be used for this.",
    ),
    "default_not_allowed": (
        409,
        "default_not_allowed",
        "Choose each default from its own list: a scan model, an answering model and a thinking level.",
    ),
    "level_not_supported": (
        409,
        "level_not_supported",
        "A chosen model does not accept one of these thinking levels. Choose levels every model accepts.",
    ),
}

Level = Literal["low", "medium", "high"]
ModelId = Annotated[str, Field(max_length=257)]  # MODEL_ID: 128 + "/" + 128 at most
ModelList = Annotated[list[ModelId], Field(max_length=MAX_ORG_MODELS)]
Levels = Annotated[list[Level], Field(min_length=1, max_length=3)]


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class OrgAiBody(BaseModel):
    """One organisation's own settings: an empty list or a null field follows the
    platform defaults; with a fresh code."""

    model_config = ConfigDict(extra="forbid")

    scan_models: ModelList
    scan_default: ModelId | None
    chat_models: ModelList
    chat_default: ModelId | None
    scan_thinking: Level | None
    chat_levels: Levels | None
    chat_default_level: Level | None
    dynamic: bool | None
    ensemble: bool
    ai_budget_usd: Decimal | None = Field(gt=0, le=MAX_BUDGET, decimal_places=2)
    code: str = Field(min_length=1, max_length=MAX_CODE)


Feature = Literal[
    "scan_ai", "ask", "ensemble", "autofile", "cost_saving", "review", "my_words", "self_signup"
]
# Sign-up makes a new organisation: one has no switch of its own for it (0039).
OrgFeature = Literal["scan_ai", "ask", "ensemble", "autofile", "cost_saving", "review", "my_words"]


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class TestModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_model: ModelId | None = None
    chat_model: ModelId | None = None
    note: str | None = Field(default=None, max_length=500)
    code: str = Field(min_length=1, max_length=MAX_CODE)


class DefaultsBody(BaseModel):
    """The platform defaults (no Ensemble, no budget: those are an organisation's own);
    empty model lists use OCR_MODEL and MODEL_CHAT, a null scan level OCR_THINKING_LEVEL."""

    model_config = ConfigDict(extra="forbid")

    scan_models: ModelList
    scan_default: ModelId | None
    chat_models: ModelList
    chat_default: ModelId | None
    scan_thinking: Level | None
    chat_levels: Levels
    chat_default_level: Level
    dynamic: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


@router.get("/models")
def list_models(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """Only the models Kaagaz can use (Tushar, 18 Oct 2026: "remove the models not feasible
    for this product"): eligible to scan or to answer, or still enabled, so it can be
    turned off. A model failing a rule (no zero-data-retention provider, no image input...)
    stays in model_catalog but is not listed."""
    with db.operator() as conn:
        rows = models_sql.list_models(conn, operator.operator_id)
    usable = (row for row in rows if row["eligible"] or row["chat_eligible"] or row["enabled_at"] is not None)
    return {"items": [_model(row, context.settings) for row in usable]}


@router.put("/models/{model:path}")
def set_model_enabled(
    model: str, body: EnableBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Enable or disable one model on the platform (MC2): a fresh code from the operator's
    own authenticator first."""
    if not valid_model_id(model):
        raise ApiError(404, "not_found")
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_model_enabled(
            conn,
            operator.operator_id,
            model,
            enabled=body.enabled,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return {"model": {"id": model, "enabled": body.enabled, "changed": outcome == "done"}}


@router.get("/ai-defaults")
def get_ai_defaults(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """The platform defaults and how many organisations follow each area now (D8)."""
    with db.operator() as conn:
        row = models_sql.ai_defaults(conn, operator.operator_id)
    if row is None:  # the operator was disabled meanwhile
        raise ApiError(401, "not_signed_in")
    return {
        "defaults": _ai(row),
        "env": _env(context.settings),
        "orgs_inheriting": {
            area: int(row[f"orgs_inheriting_{area}"])
            for area in ("scan_models", "chat_models", "scan_thinking", "chat_levels", "dynamic")
        },
    }


@router.put("/ai-defaults")
def set_ai_defaults(
    body: DefaultsBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Replace the platform defaults (C10): every organisation still on them follows."""
    choice = _choice(body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_ai_defaults(
            conn,
            operator.operator_id,
            choice,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_ai_defaults(operator, db, context)


@router.get("/orgs/{customer_id}/ai")
def get_org_ai(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """The organisation's own settings, the platform defaults it follows where it has none,
    its Ensemble switch, the most models that work together, and its budget."""
    with db.operator() as conn:
        own = models_sql.org_ai(conn, operator.operator_id, str(customer_id))
        defaults = models_sql.ai_defaults(conn, operator.operator_id)
    if own is None or defaults is None:
        raise ApiError(404, "not_found")
    return {
        "own": _ai(own),
        "defaults": _ai(defaults),
        "env": _env(context.settings),
        "ensemble": bool(own["ensemble"]),
        "max_agents": MAX_AGENTS,
        "ai_budget_usd": _number(own["ai_budget_usd"]),
    }


@router.put("/orgs/{customer_id}/ai")
def set_org_ai(
    customer_id: uuid.UUID, body: OrgAiBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Replace the organisation's AI settings, Ensemble switch and budget (C10, SP4)."""
    choice = _choice(body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_org_ai(
            conn,
            operator.operator_id,
            str(customer_id),
            choice,
            ensemble=body.ensemble,
            budget=body.ai_budget_usd,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome.code, step_up)
    saved = get_org_ai(customer_id, operator, db, context)
    # Ensemble with fewer than two scanning models in effect is saved off, not refused (0037).
    return {**saved, "ensemble_off": body.ensemble and not saved["ensemble"]}


@router.get("/feature-switches")
def get_feature_switches(operator: OperatorSession, db: OperatorDb) -> list[dict[str, Any]]:
    """Every product feature, on or off for every organisation, and how many use it now."""
    with db.operator() as conn:
        rows = models_sql.feature_switches(conn, operator.operator_id)
    return [
        {
            "feature": row["feature"],
            "enabled": bool(row["enabled"]),
            "organisations_using": int(row["organisations_using"]),
        }
        for row in rows
    ]


@router.put("/feature-switches/{feature}")
def set_feature_switch(
    feature: Feature, body: SwitchBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> list[dict[str, Any]]:
    """Flip one feature for every organisation (a fresh code first); the list comes back."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_feature_switch(
            conn, operator.operator_id, feature, enabled=body.enabled, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_feature_switches(operator, db)


@router.get("/orgs/{customer_id}/feature-switches")
def get_org_feature_switches(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb
) -> list[dict[str, Any]]:
    """One organisation's switches, each with the platform's (off there wins)."""
    with db.operator() as conn:
        rows = models_sql.org_feature_switches(conn, operator.operator_id, str(customer_id))
    if not rows:
        raise ApiError(404, "not_found")
    return [
        {
            "feature": row["feature"],
            "enabled": bool(row["enabled"]),
            "platform_enabled": bool(row["platform_enabled"]),
        }
        for row in rows
    ]


@router.put("/orgs/{customer_id}/feature-switches/{feature}")
def set_org_feature_switch(
    customer_id: uuid.UUID,
    feature: OrgFeature,
    body: SwitchBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> list[dict[str, Any]]:
    """Flip one feature for one organisation (a fresh code first); its list comes back."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_org_feature_switch(
            conn, operator.operator_id, str(customer_id), feature, enabled=body.enabled, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_feature_switches(customer_id, operator, db)


@router.get("/test-model")
def get_test_model(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    """The platform test model, and what ran on it since it started (real spend rows only)."""
    with db.operator() as conn:
        row = models_sql.current_test_model(conn, operator.operator_id)
    if row is None:
        return {
            "active": False,
            "scan_model": None,
            "chat_model": None,
            "note": None,
            "started_at": None,
            "since": None,
        }
    return {
        "active": True,
        "scan_model": row["scan_model"],
        "chat_model": row["chat_model"],
        "note": row["note"],
        "started_at": iso(row["started_at"]),
        "since": {
            "pages": int(row["pages"]),
            "questions": int(row["questions"]),
            "cost_usd": _number(row["cost_usd"]),  # what OpenRouter reported; null: none reported
            "avg_seconds_per_page": row["seconds_per_page"],
        },
    }


@router.post("/test-model")
def start_test_model(
    body: TestModelBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Every organisation scans with ``scan_model`` and answers with ``chat_model`` (either
    may be null: that area stays each organisation's own) until the undo."""
    named = [model for model in (body.scan_model, body.chat_model) if model is not None]
    if not named or not all(valid_model_id(model) for model in named):
        raise ApiError(400, "bad_request", "Choose a scanning model, an answering model or both.")
    with db.operator() as conn:
        catalogue = {row["id"]: row for row in models_sql.list_models(conn, operator.operator_id)}
    if not all(_callable(catalogue.get(model), context.settings) for model in named):
        raise ApiError(*_REFUSALS["unavailable"])
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_test_model(
            conn,
            operator.operator_id,
            scan_model=body.scan_model,
            chat_model=body.chat_model,
            note=body.note,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_test_model(operator, db)


@router.delete("/test-model")
def undo_test_model(
    body: StepUpBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Every organisation back to its own models, exactly as before (a fresh code first)."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.clear_test_model(conn, operator.operator_id, step=step_up.step)
    _refuse(db, operator, outcome, step_up)
    return get_test_model(operator, db)


def _callable(row: RowMapping | None, settings: Settings) -> bool:
    """The model is in the catalogue, its maker in OPENROUTER_MODEL_VENDORS and one of its
    providers in OPENROUTER_PROVIDERS (S1), so the app can call it."""
    return (
        row is not None
        and vendor(row["id"]) in settings.openrouter_model_vendors
        and any(slug in settings.openrouter_providers for slug in row["providers"])
    )


@router.get("/usage")
def get_usage(
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
    start: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    end: Annotated[str | None, Query(alias="to", max_length=10)] = None,
) -> dict[str, Any]:
    """Spend per organisation and model (SP3): ids, names of organisations and models,
    numbers. No user, no document."""
    zone = context.settings.timezone
    today = context.clock().astimezone(ZoneInfo(zone)).date()
    first, last = _day(start, today.replace(day=1)), _day(end, today)
    if last < first or (last - first).days >= MAX_DAYS:
        raise ApiError(400, "bad_request", f"Choose up to {MAX_DAYS} days, the first on or before the last.")
    with db.operator() as conn:
        rows = models_sql.usage(
            conn,
            operator.operator_id,
            day_start(first.isoformat(), zone),
            day_start((last + timedelta(days=1)).isoformat(), zone),
        )
    return {"from": first.isoformat(), "to": last.isoformat(), "items": [_usage(row) for row in rows]}


def _refuse(
    db: OperatorDatabase, operator: CurrentOperator, code: str, step_up: StepUp | None = None
) -> None:
    """Nothing for ``done`` and ``unchanged``; the reply for every other outcome."""
    _check(db, operator, Outcome(code, None), step_up)
    if code in _REFUSALS:
        status, error, message = _REFUSALS[code]
        raise ApiError(status, error, message)


def _day(value: str | None, default: date) -> date:
    if value is None:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "bad_request", "Give dates as YYYY-MM-DD.") from None


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _choice(body: OrgAiBody | DefaultsBody) -> AiChoice:
    """The save's models and levels, after every named model is an OpenRouter model ID
    (400 before a code is used up)."""
    named = [*body.scan_models, *body.chat_models, body.scan_default, body.chat_default]
    if not all(valid_model_id(model) for model in named if model is not None):
        raise ApiError(400, "bad_request", "A model is not an OpenRouter model ID.")
    return AiChoice(
        scan_models=body.scan_models,
        scan_default=body.scan_default,
        chat_models=body.chat_models,
        chat_default=body.chat_default,
        scan_thinking=body.scan_thinking,
        chat_levels=body.chat_levels,
        chat_default_level=body.chat_default_level,
        dynamic=body.dynamic,
    )


def _ai(row: RowMapping) -> dict[str, Any]:
    """An organisation's own settings or the platform defaults, as saved."""
    return {
        "scan_models": list(row["scan_models"]),
        "scan_default": row["scan_default"],
        "chat_models": list(row["chat_models"]),
        "chat_default": row["chat_default"],
        "scan_thinking": row["scan_thinking"],
        "chat_levels": None if row["chat_levels"] is None else list(row["chat_levels"]),
        "chat_default_level": row["chat_default_level"],
        "dynamic": row["dynamic"],
    }


def _env(settings: Settings) -> dict[str, str]:
    """What the server uses when the platform defaults name no model or scan level."""
    return {
        "scan_model": settings.ocr_model,
        "chat_model": settings.model_chat,
        "scan_thinking": settings.ocr_thinking_level,
    }


def _model(row: RowMapping, settings: Settings) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "price_in_usd_per_m": float(row["price_in_usd_per_m"]),
        "price_out_usd_per_m": float(row["price_out_usd_per_m"]),
        "providers": list(row["providers"]),
        "efforts": list(row["efforts"]),
        "expires_on": None if row["expires_on"] is None else row["expires_on"].isoformat(),
        "eligible": bool(row["eligible"]),
        "seen_at": iso(row["seen_at"]),
        "verified": row["verified_prompt_version"] == READER_PROMPT_VERSION,
        "verified_at": None if row["verified_at"] is None else iso(row["verified_at"]),
        "tokens_per_page_in": row["tokens_per_page_in"],
        "tokens_per_page_out": row["tokens_per_page_out"],
        # MC3: the average tokens a page took in the last 30 days of the ledger (every
        # organisation's, numbers only); null with none.
        "ledger_tokens_in": _number(row["ledger_tokens_in"]),
        "ledger_tokens_out": _number(row["ledger_tokens_out"]),
        # The real cost OpenRouter reported over the same 30 days (0033): scanned pages and
        # answered questions, those with a reported cost, that cost (null: none reported)
        # and the calls whose cost is not known (never guessed from the list price).
        "scan_pages": int(row["scan_pages"]),
        "scan_pages_costed": int(row["scan_pages_costed"]),
        "scan_cost_usd": _number(row["scan_cost_usd"]),
        "scan_cost_unknown_calls": int(row["scan_cost_unknown_calls"]),
        "answer_questions": int(row["answer_questions"]),
        "answer_questions_costed": int(row["answer_questions_costed"]),
        "answer_cost_usd": _number(row["answer_cost_usd"]),
        "answer_cost_unknown_calls": int(row["answer_cost_unknown_calls"]),
        "enabled_at": None if row["enabled_at"] is None else iso(row["enabled_at"]),
        # C11: may it answer questions, and did verify-models pass the answer prompt.
        "chat_eligible": bool(row["chat_eligible"]),
        "chat_verified": row["chat_verified_prompt"] == ANSWER_PROMPT_HASH,
        # S1: a maker outside OPENROUTER_MODEL_VENDORS is never used, even when enabled
        # (google and anthropic by default, Tushar 15 Oct 2026).
        "vendor_allowed": vendor(row["id"]) in settings.openrouter_model_vendors,
    }


def _usage(row: RowMapping) -> dict[str, Any]:
    """One organisation's use of one model: never a user or a document."""
    return {
        "organisation_id": str(row["customer_id"]),
        "organisation_name": row["customer_name"],
        "model": row["model"],
        "calls": int(row["calls"]),
        "cached_calls": int(row["cached_calls"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "reasoning_tokens": int(row["reasoning_tokens"]),
        "cost_usd": _number(row["cost_usd"]),
        "cost_estimated": bool(row["cost_estimated"]),
        "cost_part_unknown": bool(row["cost_part_unknown"]),  # the sum counts the known costs only
    }
