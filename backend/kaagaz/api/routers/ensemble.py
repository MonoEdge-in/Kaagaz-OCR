"""Ensemble after the scan through the API (scan review UX rules, Tushar, 16 Oct 2026,
sections 5 and 6; the council plan of 16 Oct 2026, PR 6).

* ``GET /documents/{id}/ensemble/estimate?scope=&word_keys=``: the pages, words and about
  how many seconds a run would take, for the confirm sheet ("About 3 min").
* ``POST /documents/{id}/ensemble`` {scope, word_keys}: start a run, 202 with its ``Run``;
  the same request while it is queued or running is 200 with that run, any other 409
  ``ensemble_running``.
* ``GET /documents/{id}/ensemble``: the document's latest run (404 when it has none).
* ``POST .../ensemble/{run_id}/stop`` (its finished pages stay), ``.../retry`` (202, a new run
  on the pages it did not finish) and ``.../keep-all`` (``{kept, skipped}``).
* ``POST .../ensemble/readings/{reading_id}/keep`` and ``.../revert``: one change.

Reads are for every signed-in user. Writes are for reviewers and admins (the Reviewer check
until the new roles ship, spec decision of 16 Oct 2026) of an organisation with the
Ensemble grant (403 ``ensemble_not_allowed``); Stop needs no grant, so a run can always be
stopped. Every lookup runs as the signed-in customer and names the document too, so another
customer's document, run or reading is 404, as is a rejected file. The rules live in
kaagaz/db/ensemble.py, the ``Run`` JSON and its error's wording in kaagaz/api/views.py; here
the refusals are worded (spec section 6: what happened, what it means, the next step).
"""

import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, Reviewer, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers import word_review as review_router
from kaagaz.api.views import latest_run_json, run_json
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import listing
from kaagaz.db import models as models_sql
from kaagaz.db.word_review import Refused
from kaagaz.ingestion.ports import Job

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]
WordKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Scope = Literal["word", "words", "flagged", "document"]
MAX_KEYS = 1000  # the keys one request may send; a run keeps at most ensemble.MAX_WORDS

MESSAGES = {
    **review_router.MESSAGES,
    "ensemble_running": "Ensemble is already reading this document. Wait for it to finish, or stop it first.",
    "rate_limited": f"Your organisation started {ensemble_sql.RUNS_PER_HOUR} Ensemble runs in the last hour."
    " Start this one again later.",
    "too_many_pages": f"Ensemble reads up to {ensemble_sql.MAX_DOCUMENT_PAGES} scanned pages at a time."
    " Run it on the flagged words instead.",
    "no_pages": "This document has no scanned pages. Ensemble reads scanned pages only.",
    "no_words": "Every flagged word is already reviewed or resolved. Nothing is left for Ensemble.",
    "already_decided": "This change was already kept or reverted. Reload to see the latest text.",
    "not_retryable": "This run has no pages left to read again.",
}
STATUS = {
    **review_router.STATUS,
    "ensemble_running": 409,
    "rate_limited": 429,
    "too_many_pages": 409,
    "no_pages": 409,
    "no_words": 409,
    "already_decided": 409,
    "not_retryable": 409,
}
BAD_SCOPE = "Send one word for this word, at least one for selected words, and none for the others."


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Scope
    word_keys: list[WordKey] = Field(default_factory=list, max_length=MAX_KEYS)


def _document(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> None:
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None or row["status"] == "rejected":
        raise ApiError(404, "not_found")


def _refused(error: Refused) -> ApiError:
    return ApiError(STATUS.get(error.code, 409), error.code, MESSAGES.get(error.code))


def _others(conn: Connection, context: AppContext, user: CurrentUser, document_id: uuid.UUID) -> list[str]:
    """The models that read again (``models.resolve_ensemble``), or 403."""
    try:
        reading = models_sql.resolve_ensemble(
            conn, user.customer_id, str(document_id), settings=context.settings
        )
    except models_sql.AiNotAllowed:
        raise ApiError(403, "ensemble_not_allowed", review_router.ENSEMBLE_OFF) from None
    return [agent.model for agent in reading.others]


def _granted(conn: Connection, user: CurrentUser) -> None:
    if not models_sql.effective_settings(conn, user.customer_id)["ensemble"]:
        raise ApiError(403, "ensemble_not_allowed", review_router.ENSEMBLE_OFF)


def _check_keys(scope: str, keys: Sequence[str]) -> None:
    count = len(keys)
    if (
        (scope == "word" and count != 1)
        or (scope == "words" and not count)
        or (scope in ("flagged", "document") and count)
    ):
        raise ApiError(400, "bad_request", BAD_SCOPE)


@router.get("/documents/{document_id}/ensemble/estimate")
def estimate_run(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    scope: Scope,
    word_keys: Annotated[list[WordKey] | None, Query(max_length=MAX_KEYS)] = None,
) -> dict[str, int]:
    keys = word_keys or []
    _check_keys(scope, keys)
    with context.db.customer(user.customer_id) as conn:
        _document(conn, user, document_id)
        try:
            return ensemble_sql.estimate(conn, user.customer_id, str(document_id), scope, keys)
        except Refused as error:
            raise _refused(error) from None


@router.post("/documents/{document_id}/ensemble", status_code=202)
def start_run(
    document_id: uuid.UUID, body: StartBody, user: Reviewer, context: Context, response: Response
) -> dict[str, Any]:
    _check_keys(body.scope, body.word_keys)
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        others = _others(conn, context, user, document_id)
        try:
            run, made = ensemble_sql.start(
                conn, customer_id, target, scope=body.scope, word_keys=body.word_keys, models=others,
                user_id=user.user_id,
            )  # fmt: skip
        except Refused as error:
            raise _refused(error) from None
        reply = run_json(conn, customer_id, run)
        if made:  # last, inside the transaction: a queue that is down rolls the run back (503)
            context.queue.enqueue(Job(customer_id, target, run_id=str(run["id"])))
        else:
            response.status_code = 200
    return reply


@router.get("/documents/{document_id}/ensemble")
def get_run(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        _document(conn, user, document_id)
        reply = latest_run_json(conn, user.customer_id, str(document_id))
    if reply is None:
        raise ApiError(404, "not_found")
    return reply


@router.post("/documents/{document_id}/ensemble/{run_id}/stop")
def stop_run(document_id: uuid.UUID, run_id: uuid.UUID, user: Reviewer, context: Context) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        try:
            ensemble_sql.stop(
                conn, customer_id, target, str(run_id), user_id=user.user_id, now=context.clock()
            )
        except Refused as error:
            raise _refused(error) from None
        run = ensemble_sql.get_run(conn, customer_id, target, str(run_id))
        if run is None:  # gone with its document in the moment since
            raise ApiError(404, "not_found")
        return run_json(conn, customer_id, run)


@router.post("/documents/{document_id}/ensemble/{run_id}/retry", status_code=202)
def retry_run(document_id: uuid.UUID, run_id: uuid.UUID, user: Reviewer, context: Context) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        others = _others(conn, context, user, document_id)
        try:
            run = ensemble_sql.retry(
                conn, customer_id, target, str(run_id), models=others, user_id=user.user_id
            )
        except Refused as error:
            raise _refused(error) from None
        reply = run_json(conn, customer_id, run)
        context.queue.enqueue(Job(customer_id, target, run_id=str(run["id"])))  # last, as for a start
    return reply


@router.post("/documents/{document_id}/ensemble/{run_id}/keep-all")
def keep_all(document_id: uuid.UUID, run_id: uuid.UUID, user: Reviewer, context: Context) -> dict[str, int]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        _granted(conn, user)
        try:
            return ensemble_sql.keep_all(
                conn, customer_id, str(document_id), str(run_id), user_id=user.user_id, now=context.clock()
            )
        except Refused as error:
            raise _refused(error) from None


@router.post("/documents/{document_id}/ensemble/readings/{reading_id}/keep")
def keep_reading(
    document_id: uuid.UUID, reading_id: uuid.UUID, user: Reviewer, context: Context
) -> dict[str, Any]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        _granted(conn, user)
        try:
            change_id = ensemble_sql.keep(
                conn,
                customer_id,
                str(document_id),
                str(reading_id),
                user_id=user.user_id,
                now=context.clock(),
            )
        except Refused as error:
            raise _refused(error) from None
    return {"id": str(reading_id), "decision": "kept", "change_id": change_id}


@router.post("/documents/{document_id}/ensemble/readings/{reading_id}/revert")
def revert_reading(
    document_id: uuid.UUID, reading_id: uuid.UUID, user: Reviewer, context: Context
) -> dict[str, Any]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        _granted(conn, user)
        try:
            ensemble_sql.revert(
                conn,
                customer_id,
                str(document_id),
                str(reading_id),
                user_id=user.user_id,
                now=context.clock(),
            )
        except Refused as error:
            raise _refused(error) from None
    return {"id": str(reading_id), "decision": "reverted"}
