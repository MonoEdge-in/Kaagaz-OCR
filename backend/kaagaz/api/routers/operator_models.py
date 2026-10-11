"""The platform operator's models and AI spend (model choice, council 11 Oct 2026, MC2,
MC3, MS1, MS2, SP3, SP4). Every route needs the operator cookie (``current_operator`` on
the router); only ``OperatorDatabase`` here, and each call is one ``kaagaz_op_*``
function (``kaagaz.db.operator_models``), which writes its own audit rows.

* ``GET /operator/models``: the catalogue, from OpenRouter's live lists (``jobs/catalog``),
  with ``verified`` for the reader's current prompt (``READER_PROMPT_VERSION``).
* ``PUT /operator/models/{model}`` {enabled, code} (step-up, as 0016's): enable or disable
  one model on the platform. Only an eligible model verified for the current prompt can be
  enabled; one an organisation still has can't be disabled.
* ``GET`` and ``PUT /operator/orgs/{id}/models`` {models, default_scan, ai_budget_usd}: the
  models an organisation may read scans with, its default and its monthly budget (warn
  only). No models and no default: the platform's model (OCR_MODEL), as before.
* ``GET /operator/usage?from&to``: tokens and dollars per organisation and model, days in
  TIMEZONE with both ends included (this month so far when left out), at most
  ``MAX_DAYS``. Never a user or a document: the function has no column for either.

A refused change is said in plain words; an unknown organisation or model gets 404.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping

from kaagaz.api.auth import CurrentOperator, OperatorSession, current_operator
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.routers.documents import day_start
from kaagaz.api.routers.operator_orgs import StepUp, _check, _step_up
from kaagaz.api.views import iso
from kaagaz.db import operator_models as models_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_orgs import Outcome
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.settings import valid_model_id

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

MAX_DAYS = 366
MAX_ORG_MODELS = 64
# numeric(12,2): the largest budget the column holds.
MAX_BUDGET = Decimal("9999999999.99")
# The outcomes kaagaz.db.operator_orgs' _check does not know, and their replies.
_REFUSALS = {
    "not_eligible": (409, "not_eligible", "This model does not pass the catalogue's rules now."),
    "not_verified": (
        409,
        "not_verified",
        "This model has not passed verify-models for the reader's current prompt.",
    ),
    "in_use": (409, "in_use", "An organisation still reads with this model. Remove it there first."),
    "unavailable": (409, "unavailable", "One of these models is not enabled on the platform now."),
    "default_not_allowed": (
        400,
        "default_not_allowed",
        "Choose the default scan model from the allowed models, or no default with no models.",
    ),
}


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class OrgModelsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[Annotated[str, Field(max_length=200)]] = Field(max_length=MAX_ORG_MODELS)
    default_scan: str | None = Field(max_length=200)
    ai_budget_usd: Decimal | None = Field(gt=0, le=MAX_BUDGET, decimal_places=2)


@router.get("/models")
def list_models(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    with db.operator() as conn:
        rows = models_sql.list_models(conn, operator.operator_id)
    return {"items": [_model(row) for row in rows]}


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
            prompt_version=READER_PROMPT_VERSION,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return {"model": {"id": model, "enabled": body.enabled, "changed": outcome == "done"}}


@router.get("/orgs/{customer_id}/models")
def get_org_models(customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    with db.operator() as conn:
        row = models_sql.org_models(conn, operator.operator_id, str(customer_id))
    return _org_models(row)


@router.put("/orgs/{customer_id}/models")
def set_org_models(
    customer_id: uuid.UUID, body: OrgModelsBody, operator: OperatorSession, db: OperatorDb
) -> dict[str, Any]:
    """Replace the organisation's models, default and budget (MS1, MS2, SP4)."""
    named = [*body.models, *([] if body.default_scan is None else [body.default_scan])]
    if not all(map(valid_model_id, named)):
        raise ApiError(400, "bad_request", "A model is not an OpenRouter model ID.")
    with db.operator() as conn:
        outcome = models_sql.set_org_models(
            conn,
            operator.operator_id,
            str(customer_id),
            models=body.models,
            default_scan=body.default_scan,
            budget=body.ai_budget_usd,
            prompt_version=READER_PROMPT_VERSION,
        )
        row = models_sql.org_models(conn, operator.operator_id, str(customer_id))
    _refuse(db, operator, outcome.code)
    return _org_models(row)


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


def _model(row: RowMapping) -> dict[str, Any]:
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
        "enabled_at": None if row["enabled_at"] is None else iso(row["enabled_at"]),
    }


def _org_models(row: RowMapping | None) -> dict[str, Any]:
    if row is None:
        raise ApiError(404, "not_found")
    return {
        "models": list(row["models"]),
        "default_scan": row["default_scan"],
        "ai_budget_usd": _number(row["ai_budget_usd"]),
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
