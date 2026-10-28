"""Word-level review of one document (scan review UX rules, Tushar, 16 Oct 2026; the
council plan of 16 Oct 2026): the summary bar and export status, the flagged words, a
reviewer's Confirm and Edit, the history and Undo.

* ``GET /documents/{id}/review-status``: pages, words, flagged (by band), reviewed,
  resolved, remaining and the last change time; the summary bar's sentence once the scan
  is complete, the export status, and whether Ensemble may run, with its latest run
  (``run``, kaagaz.api.views.run_json's ``Run``; null when there is none).
* ``GET /documents/{id}/flags``: the flagged words, optionally of one page or one state.
  Members see each flag with its band, reason and score but no other readings. A word an
  active Ensemble run will read is ``in_ensemble``, one with a change Ensemble proposes
  ``proposed`` (while it is open); Ensemble's readings come first among the alternatives,
  and reviewers see its latest reading (``ensemble``) to Keep or Revert.
* ``POST /documents/{id}/flags/{word_key}/confirm`` and ``.../edit``: a reviewer or an admin
  (spec decision of 16 Oct 2026: the Reviewer check until the new roles ship). The body
  names the text the person saw (``expected_text``), so a word changed meanwhile is 409
  ``changed_since``, never overwritten.
* ``GET /documents/{id}/changes`` and ``POST .../changes/{change_id}/undo``: the history,
  newest first (``source`` person or ensemble), and Undo of the latest change.

Every lookup runs as the signed-in customer, so another customer's document, word or
change is 404, as is a rejected file (like the page readings). The rules live in
kaagaz/db/word_review.py; here the JSON is shaped and the wording kept.
"""

import uuid
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import CurrentUser, Reviewer, User, require_feature
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.readers import usable
from kaagaz.api.views import latest_run_json
from kaagaz.db import ai_settings, listing, word_review
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import models as models_sql
from kaagaz.extraction.flags import REASONS, Flag, alternatives

router = APIRouter(dependencies=[Depends(require_feature("review"))])  # 0038: 403 feature_off

Context = Annotated[AppContext, Depends(get_context)]
MAX_TEXT = 5000  # characters a person may type or send back for one word or line
REVIEWERS = frozenset({"reviewer", "admin"})

# What the user reads (spec section 6: what happened, what it means, the next step).
MESSAGES = {
    "not_ready": "This document is still being read. Its words can be reviewed when scanning finishes.",
    "in_trash": "This document is in the Trash. Restore it first.",
    "changed_since": "This word was changed since you opened it. Your change was not saved."
    " Reload to see the latest text.",
    "not_latest": "Only the latest change can be undone. Undo the changes after it first.",
    "empty_text": "Type the correct text.",
}
STATUS = {"not_found": 404, "in_trash": 409, "not_ready": 409, "changed_since": 409, "not_latest": 409}
ENSEMBLE_WAIT = "Ensemble is available when scanning finishes."
ENSEMBLE_ROLE = "Only reviewers and admins can run Ensemble."
ENSEMBLE_OFF = "Ensemble is not turned on for your organisation."
GONE = "No longer on the page"
IN_ENSEMBLE, PROPOSED = "in_ensemble", "proposed"


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_text: str = Field(max_length=MAX_TEXT)


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_TEXT)
    expected_text: str = Field(max_length=MAX_TEXT)


def counted(number: int, noun: str) -> str:
    """ "1 page", "2,431 words"."""
    return f"{number:,} {noun}" + ("" if number == 1 else "s")


def summary_text(pages: int, words: int, flagged: int) -> str:
    """The summary bar once the scan is complete (spec section 3)."""
    flags = f"{flagged:,} flagged for review" if flagged else "Nothing flagged"
    return f"Scan complete · {counted(pages, 'page')} · {counted(words, 'word')} · {flags}"


def export_text(flagged: int, done: int, remaining: int) -> str:
    """The export dialog's review status (spec section 7)."""
    if not flagged:
        return "Nothing flagged. Ready to export."
    if not remaining:
        return f"{flagged:,} flagged · {done:,} reviewed. Ready to export."
    return f"{counted(remaining, 'flagged word')} not yet reviewed. Export anyway or review first."


def _document(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None or row["status"] == "rejected":
        raise ApiError(404, "not_found")
    return row


def _refused(error: word_review.Refused) -> ApiError:
    return ApiError(STATUS[error.code], error.code, MESSAGES.get(error.code))


def _at(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def flag_json(
    flag: Flag,
    latest: Mapping[str, RowMapping],
    user: CurrentUser,
    readings: Mapping[str, RowMapping] | None = None,
    running: Collection[str] = (),
) -> dict[str, Any]:
    """``WordFlag``: one flagged word, its state and its latest change in force; with
    ``readings`` (each word's latest Ensemble reading) and ``running`` (the words an active
    run will read), its Ensemble state and readings."""
    change = latest.get(flag.key)
    reading = (readings or {}).get(flag.key)
    text = word_review.current_text(flag, latest)
    state = word_review.state(flag, latest)
    if state == word_review.OPEN and reading is not None and reading["outcome"] == "changed":
        state = PROPOSED if reading["decision"] is None else state
    if state == word_review.OPEN and flag.key in running:
        state = IN_ENSEMBLE
    others = flag.alternatives
    if reading is not None:
        others = alternatives(text, [*(read["text"] for read in reading["reads"]), *flag.alternatives])
    reviewer = user.role in REVIEWERS
    return {
        "word_key": flag.key,
        "kind": flag.kind,
        "page_no": flag.page_no,
        "region": flag.region,
        "as_read": flag.as_read,
        "text": text,
        "band": flag.band,
        "reason": flag.reason,
        "reason_text": REASONS[flag.reason],
        "score": flag.score,
        "alternatives": list(others) if reviewer else None,
        "value_id": flag.value_id,
        "state": state,
        "ensemble": None
        if reading is None or not reviewer
        else {
            "reading_id": str(reading["id"]),
            "run_id": str(reading["run_id"]),
            "outcome": reading["outcome"],
            "proposal": reading["proposal"],
            "decision": reading["decision"],
        },
        "change": None
        if change is None
        else {
            "id": str(change["id"]),
            "action": change["action"],
            "at": _at(change["created_at"]),
            "by_you": str(change["user_id"]) == user.user_id,
        },
    }


def change_json(
    row: RowMapping, *, on_page: bool, latest_id: str | None, user: CurrentUser
) -> dict[str, Any]:
    """``WordChange``: one row of the history."""
    person = row["action"] in word_review.PERSON_ACTIONS
    return {
        "id": str(row["id"]),
        "word_key": row["word_key"],
        "kind": row["kind"],
        "page_no": row["page_no"],
        "region": row["region"],
        "action": row["action"],
        "before_text": row["before_text"],
        "after_text": row["after_text"],
        "by": "person" if person else "ensemble",
        "by_you": row["user_id"] is not None and str(row["user_id"]) == user.user_id,
        "user_name": row["user_name"],
        "created_at": _at(row["created_at"]),
        "undone_at": _at(row["undone_at"]),
        "on_page": on_page,
        "note": None if on_page else GONE,
        "can_undo": user.role in REVIEWERS and str(row["id"]) == latest_id,
    }


def _ensemble(conn: Connection, context: AppContext, user: CurrentUser, row: RowMapping) -> dict[str, Any]:
    """Whether Ensemble may run on this document now, and if not, why."""
    reason = None
    if row["status"] != "ready":
        reason = ENSEMBLE_WAIT
    elif user.role not in REVIEWERS:
        reason = ENSEMBLE_ROLE
    elif len(usable(conn, user.customer_id, context.settings)) < 2:
        reason = ENSEMBLE_OFF
    else:
        granted = bool(models_sql.effective_settings(conn, user.customer_id)["ensemble"])
        if not ai_settings.task_on(conn, user.customer_id, user.user_id, "ensemble", granted, granted):
            reason = ENSEMBLE_OFF  # AI settings (0038): this person's Ensemble too
    run = latest_run_json(conn, user.customer_id, str(row["id"]))
    return {"allowed": reason is None, "reason": reason, "run": run}


@router.get("/documents/{document_id}/review-status")
def get_review_status(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document(conn, user, document_id)
        review = word_review.load(conn, customer_id, target)
        numbers = word_review.counts(conn, customer_id, target, review)
        ensemble = _ensemble(conn, context, user, row)
    ready = row["status"] == "ready"
    done = numbers["reviewed"] + numbers["resolved"]
    return {
        **numbers,
        "last_change_at": _at(numbers["last_change_at"]),
        "status": row["status"],
        "summary": summary_text(numbers["pages"], numbers["words"], numbers["flagged"]) if ready else None,
        "export": {
            "ready": numbers["remaining"] == 0,
            "message": export_text(numbers["flagged"], done, numbers["remaining"]),
        },
        "can_review": user.role in REVIEWERS,
        "ensemble": ensemble,
    }


@router.get("/documents/{document_id}/flags")
def list_flags(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    page: Annotated[int | None, Query(ge=1)] = None,
    state: Literal["open", "reviewed", "resolved", "in_ensemble", "proposed"] | None = None,
) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        review = word_review.load(conn, customer_id, target)
        readings = ensemble_sql.latest_readings(conn, customer_id, target)
        run = ensemble_sql.latest_run(conn, customer_id, target)
    running: set[str] = set()
    if run is not None and run["status"] in ensemble_sql.ACTIVE:
        left = set(run["pages"]) - set(run["pages_done"])
        keys = None if run["word_keys"] is None else set(run["word_keys"])
        running = {
            flag.key for flag in review.flags if flag.page_no in left and (keys is None or flag.key in keys)
        }
    latest = review.latest()
    items = [
        flag_json(flag, latest, user, readings, running)
        for flag in review.listed
        if page is None or flag.page_no == page
    ]
    items = [item for item in items if state is None or item["state"] == state]
    return {"items": items, "total": len(items)}


def _change(
    context: AppContext, user: CurrentUser, document_id: uuid.UUID, key: str, **asked: Any
) -> dict[str, Any]:
    """Confirm or edit one word in one transaction, then return its flag as it is now."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        try:
            word_review.change_word(
                conn, customer_id, target, key, user_id=user.user_id, now=context.clock(), **asked
            )
        except word_review.Refused as error:
            raise _refused(error) from None
        review = word_review.load(conn, customer_id, target)
        flag = review.flag(key)
        if flag is None:  # its value was dropped meanwhile; raising rolls the change back
            raise ApiError(404, "not_found")
    return flag_json(flag, review.latest(), user)


@router.post("/documents/{document_id}/flags/{word_key}/confirm")
def confirm_word(
    document_id: uuid.UUID, word_key: str, body: ConfirmBody, user: Reviewer, context: Context
) -> dict[str, Any]:
    return _change(context, user, document_id, word_key, expected_text=body.expected_text, new_text=None)


@router.post("/documents/{document_id}/flags/{word_key}/edit")
def edit_word(
    document_id: uuid.UUID, word_key: str, body: EditBody, user: Reviewer, context: Context
) -> dict[str, Any]:
    typed = body.text.strip()
    if not typed:
        raise ApiError(400, "bad_request", MESSAGES["empty_text"])
    return _change(context, user, document_id, word_key, expected_text=body.expected_text, new_text=typed)


def _history(review: word_review.Review, user: CurrentUser) -> list[dict[str, Any]]:
    """Every change, newest first."""
    keys = {flag.key for flag in review.flags}
    live = [row for row in review.changes if row["undone_at"] is None]
    latest_id = str(live[-1]["id"]) if live else None
    return [
        change_json(row, on_page=row["word_key"] in keys, latest_id=latest_id, user=user)
        for row in reversed(review.changes)
    ]


@router.get("/documents/{document_id}/changes")
def list_changes(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    source: Literal["person", "ensemble"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        review = word_review.load(conn, customer_id, str(document_id))
    items = [item for item in _history(review, user) if source is None or item["by"] == source]
    if before is not None:
        ids = [item["id"] for item in items]
        if str(before) not in ids:
            raise ApiError(404, "not_found")
        items = items[ids.index(str(before)) + 1 :]
    shown = items[:limit]
    return {"items": shown, "next_before": shown[-1]["id"] if len(items) > limit else None}


@router.post("/documents/{document_id}/changes/{change_id}/undo")
def undo_change(
    document_id: uuid.UUID, change_id: uuid.UUID, user: Reviewer, context: Context
) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document(conn, user, document_id)
        try:
            word_review.undo_change(
                conn, customer_id, target, str(change_id), user_id=user.user_id, now=context.clock()
            )
        except word_review.Refused as error:
            raise _refused(error) from None
        review = word_review.load(conn, customer_id, target)
    return next(item for item in _history(review, user) if item["id"] == str(change_id))
