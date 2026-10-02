"""My words (council, 3 Oct 2026): a user's own words and meanings, and their spots.

Why some choices:

* **Only mine.** Every route works as the signed-in user (``Database.user``):
  another user's word, the same customer's included, is 404, never 403.
* **The second meaning.** The same word with another meaning (POST, or a PATCH
  that renames onto a word with meanings), without
  ``confirm_another``, is 409 ``word_has_meaning`` with
  ``details.existing: [{id, meaning}]``, so the screen can ask (add both, replace
  one, cancel) and send again with ``confirm_another`` or ``replace_id``. The
  same word and meaning again is 200 with the row that is there. At most 3
  meanings per word and 500 words per user (409 ``too_many_meanings``, ``too_many_words``).
* **The text** is checked by ``kaagaz.answering.words_text`` (400 saying why). A
  word chat already knows (the Hinglish map) is kept, with ``warning: "built_in"``.
* **Where it was added.** ``document_ids`` (an upload, at most 20) and ``spot``
  (a value or line on a viewer page; no box drawing in v1) must be this
  customer's documents (404), and the spot's page in the document (400). The
  region is checked strictly (pydantic, 400) and again by the table's CHECKs.
  Nothing is written before every check passed.
* **The session check.** Every write may send ``user_id``, who the tab had signed
  in; someone else signed in since gets 409 ``session_changed``, as for uploads.
* Audit rows hold ids and counts only; words and meanings are never logged. The
  rate limit is the middleware's, as for every route.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection

from kaagaz.answering.words_text import (
    MEANING_MAX,
    WORD_MAX,
    WordText,
    WordTextError,
    check_meaning,
    check_word,
    fold,
    is_built_in,
)
from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import _same_user
from kaagaz.db import audit, listing
from kaagaz.db import user_words as words_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_DOCUMENTS = 20
SESSION_CHANGED = (
    "Not saved. A different account has signed in on this browser since this page opened."
    " Reload the page to see who is signed in now."
)
HAS_MEANING = "You already added this word with another meaning. Two meanings for one word can confuse chat."
_REFUSED = {
    "not_found": (404, None),
    "conflict": (409, "You already have this word with this meaning."),
    "not_same_word": (400, "Only a meaning of the same word can be replaced."),
    "too_many_meanings": (409, "A word can have at most 3 meanings. Change or delete one first."),
    "too_many_words": (409, "You can keep at most 500 words. Delete some first."),
}
_TEXT = {
    "empty": "Type the {what}.",
    "too_long": "The {what} can be at most {limit} characters.",
    "characters": "Use only letters, digits, spaces{commas} and hyphens in the {what}.",
    "no_letter": "The {what} needs at least one letter.",
    "too_many_parts": "A word can have at most 3 parts.",
    "common_word": "Chat already reads this word its own way, so it can't be one of your words.",
}

# A fraction of the page: a finite number from 0 to 1, never a string. A count from 1.
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]
Count = Annotated[int, Field(ge=1, strict=True)]
Cell = Annotated[int, Field(ge=1, le=10_000, strict=True)]  # a row or column; keeps the region small


class BoxRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["box"]
    page: Count
    box: tuple[Fraction, Fraction, Fraction, Fraction]

    @model_validator(mode="after")
    def _corners_in_order(self) -> "BoxRegion":
        x0, y0, x1, y1 = self.box
        if not (x0 < x1 and y0 < y1):
            raise ValueError("the box's corners are out of order")
        return self


class CellRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cell"]
    page: Count
    row: Cell
    column: Cell


class SpotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    page_no: Count
    region: Annotated[BoxRegion | CellRegion, Field(discriminator="kind")]

    @model_validator(mode="after")
    def _same_page(self) -> "SpotBody":
        if self.region.page != self.page_no:
            raise ValueError("the region is on another page")
        return self


class NewWordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str = Field(max_length=4 * WORD_MAX)  # before tidying; words_text checks the real limit
    meaning: str = Field(max_length=4 * MEANING_MAX)
    confirm_another: bool = False
    replace_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_DOCUMENTS)
    spot: SpotBody | None = None
    user_id: str | None = None


class WordChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str | None = Field(default=None, max_length=4 * WORD_MAX)
    meaning: str | None = Field(default=None, max_length=4 * MEANING_MAX)
    confirm_another: bool = False
    replace_id: uuid.UUID | None = None
    user_id: str | None = None


def _word_text(value: str) -> tuple[WordText, bool]:
    try:
        checked = check_word(value)
    except WordTextError as error:
        raise _text_refused(error.reason, "word", WORD_MAX) from None
    return checked.word, checked.built_in


def _meaning_text(value: str) -> WordText:
    try:
        return check_meaning(value)
    except WordTextError as error:
        raise _text_refused(error.reason, "meaning", MEANING_MAX) from None


def _text_refused(reason: str, what: str, limit: int) -> ApiError:
    commas = ", commas" if what == "meaning" else ""
    return ApiError(400, "bad_request", _TEXT[reason].format(what=what, limit=limit, commas=commas))


@contextmanager
def _refused() -> Iterator[None]:
    """A change the SQL refused becomes its API error."""
    try:
        yield
    except words_sql.WordError as error:
        status, message = _REFUSED[error.code]
        raise ApiError(status, error.code, message) from None


def _check_documents(
    conn: Connection, customer_id: str, document_ids: list[str], spot: SpotBody | None
) -> None:
    """Every document named is this customer's (404), and the spot's page is in it
    (400). O(k log D) for k documents."""
    for document_id in document_ids:
        if listing.get_document(conn, customer_id, document_id) is None:
            raise ApiError(404, "not_found")
    if spot is not None:
        row = listing.get_document(conn, customer_id, str(spot.document_id))
        if row is None:
            raise ApiError(404, "not_found")
        if row["page_count"] is None or spot.page_no > row["page_count"]:
            raise ApiError(400, "bad_request", "That page is not in this document.")


def _audit(
    conn: Connection, user: CurrentUser, action: str, word_id: str, **details: audit.DetailValue
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="user_word",
        target_id=word_id,
        details=details,
    )


def _ask(has: words_sql.HasMeaning) -> NoReturn:
    existing = [{"id": row.id, "meaning": row.meaning} for row in has.existing]
    raise ApiError(409, "word_has_meaning", HAS_MEANING, details={"existing": existing})


def _view(row: words_sql.WordRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "word": row.word,
        "meaning": row.meaning,
        "as_read": row.as_read,
        "source": row.source,
        "built_in": is_built_in(fold(row.word)),  # the Hinglish map reads it too: the screen's badge
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/me/words")
def list_my_words(user: User, context: Context) -> dict[str, Any]:
    """Every word of this user, at most 500 (the screen filters them). One query."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = words_sql.list_words(conn, user.customer_id, user.user_id)
    return {"items": [_view(row) for row in rows]}


@router.post("/me/words", status_code=201)
def add_my_word(body: NewWordBody, user: User, context: Context, response: Response) -> dict[str, Any]:
    """201 created; 200 already there, or replaced; 409 ``word_has_meaning`` to ask
    first. O(k log D) for the k documents, plus ``add_word``'s O(log W + m)."""
    _same_user(body.user_id, user, SESSION_CHANGED)
    word, built_in = _word_text(body.word)
    meaning = _meaning_text(body.meaning)
    documents = list(dict.fromkeys(str(document_id) for document_id in body.document_ids))
    source: Literal["manual", "upload", "viewer"] = (
        "viewer" if body.spot else "upload" if documents else "manual"
    )
    customer_id, me = user.customer_id, user.user_id
    with _refused(), context.db.user(customer_id, me) as conn:
        _check_documents(conn, customer_id, documents, body.spot)
        result = words_sql.add_word(
            conn,
            customer_id,
            me,
            word=word,
            meaning=meaning,
            source=source,
            confirm_another=body.confirm_another,
            replace_id=None if body.replace_id is None else str(body.replace_id),
        )
        if isinstance(result, words_sql.HasMeaning):  # nothing was written
            _ask(result)
        word_id = result.word.id
        for document_id in documents:
            words_sql.add_spot(conn, customer_id, me, word_id=word_id, document_id=document_id)
        if body.spot is not None:
            spot = body.spot
            words_sql.add_spot(
                conn,
                customer_id,
                me,
                word_id=word_id,
                document_id=str(spot.document_id),
                page_no=spot.page_no,
                region=spot.region.model_dump(),
            )
        if result.outcome != "already" or documents or body.spot is not None:
            action = "user_word.changed" if result.outcome == "replaced" else "user_word.added"
            _audit(
                conn,
                user,
                action,
                word_id,
                outcome=result.outcome,
                documents=len(documents),
                spot=bool(body.spot),
            )
    if result.outcome != "created":
        response.status_code = 200
    return {
        "word": _view(result.word),
        "outcome": result.outcome,
        "warning": "built_in" if built_in else None,
    }


@router.patch("/me/words/{word_id}")
def change_my_word(word_id: uuid.UUID, body: WordChangeBody, user: User, context: Context) -> dict[str, Any]:
    """Change the word, its meaning or both. 404 for a word that is not this user's."""
    _same_user(body.user_id, user, SESSION_CHANGED)
    if body.word is None and body.meaning is None:
        raise ApiError(400, "bad_request", "Change the word or its meaning.")
    word, built_in = (None, False) if body.word is None else _word_text(body.word)
    meaning = None if body.meaning is None else _meaning_text(body.meaning)
    with _refused(), context.db.user(user.customer_id, user.user_id) as conn:
        row = words_sql.update_word(
            conn,
            user.customer_id,
            user.user_id,
            str(word_id),
            word=word,
            meaning=meaning,
            confirm_another=body.confirm_another,
            replace_id=None if body.replace_id is None else str(body.replace_id),
        )
        if isinstance(row, words_sql.HasMeaning):  # moved onto a word with meanings: ask first
            _ask(row)
        _audit(conn, user, "user_word.changed", row.id, word=word is not None, meaning=meaning is not None)
    return {"word": _view(row), "warning": "built_in" if built_in else None}


@router.delete("/me/words/{word_id}", status_code=204)
def delete_my_word(
    word_id: uuid.UUID, user: User, context: Context, user_id: Annotated[str | None, Query()] = None
) -> Response:
    """Delete one word of this user, and its spots. 404 for a word that is not this user's."""
    _same_user(user_id, user, SESSION_CHANGED)
    with context.db.user(user.customer_id, user.user_id) as conn:
        if not words_sql.delete_word(conn, user.customer_id, user.user_id, str(word_id)):
            raise ApiError(404, "not_found")
        _audit(conn, user, "user_word.deleted", str(word_id))
    return Response(status_code=204)


@router.get("/documents/{document_id}/my-words")
def my_words_on_page(
    document_id: uuid.UUID, page: Annotated[int, Query(ge=1)], user: User, context: Context
) -> dict[str, Any]:
    """This user's spots on one page of a document of this customer (404 otherwise),
    oldest first. Two indexed queries: O(log D + log S + k) for the page's k spots."""
    customer_id, me, target = user.customer_id, user.user_id, str(document_id)
    with context.db.user(customer_id, me) as conn:
        if listing.get_document(conn, customer_id, target) is None:
            raise ApiError(404, "not_found")
        spots = words_sql.spots_on_page(conn, customer_id, me, target, page)
    return {"items": [asdict(spot) for spot in spots]}
