"""My words (0008): one user's own words and meanings, and where they were added.

Every function takes a connection from ``Database.user(customer_id, user_id)``
(the restrictive policy hides every row from any other transaction), and every
statement also filters on ``customer_id = :c AND user_id = :me``.

``add_word``, in order: 1. lock the user's ``users`` row (FOR NO KEY UPDATE, so
an upload's key to the user does not wait), so one user's adds run one at a
time; 2. the same word and meaning (by the folds) is ``already``; 3. at most
MAX_MEANINGS meanings per word and MAX_WORDS rows per user; 4. another meaning
without ``confirm_another`` is a ``HasMeaning``, so the screen can ask; 5. one
INSERT ... ON CONFLICT DO NOTHING RETURNING (the UNIQUE on the folds is the last
guard). ``replace_id`` changes one meaning of the same word, like ``update_word``.

The text comes checked and folded from ``kaagaz.answering.words_text``. Errors
carry the API code; nothing is written before one. Words are never logged.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, text

from kaagaz.answering.words_text import WordText

MAX_WORDS = 500
MAX_MEANINGS = 3


@dataclass(frozen=True, slots=True)
class WordRow:
    id: str
    word: str
    meaning: str
    as_read: str | None
    source: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Added:
    """``created`` (a new row), ``already`` (the same word and meaning were there)
    or ``replaced`` (``replace_id``'s meaning was changed)."""

    word: WordRow
    outcome: Literal["created", "already", "replaced"]


@dataclass(frozen=True, slots=True)
class HasMeaning:
    """The word is there with other meanings, oldest first; nothing was written."""

    existing: list[WordRow]


@dataclass(frozen=True, slots=True)
class SpotRow:
    id: str
    word_id: str
    word: str
    meaning: str
    page_no: int
    region: dict[str, Any]


class WordError(Exception):
    """A change that is refused. ``code`` is the API error code: not_found (no such
    word of this user), conflict (the user has this word with this meaning),
    not_same_word (``replace_id`` is another word), too_many_meanings or too_many_words."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_LOCK_USER = "SELECT id FROM users WHERE customer_id = :c AND id = :me FOR NO KEY UPDATE"

_LIST = (
    "SELECT id, word, meaning, as_read, source, created_at, updated_at, word_fold, meaning_fold"
    " FROM user_words WHERE customer_id = :c AND user_id = :me"
    " ORDER BY word_fold, meaning_fold, id LIMIT :limit"
)

_GET = (
    "SELECT id, word, meaning, as_read, source, created_at, updated_at, word_fold, meaning_fold"
    " FROM user_words WHERE customer_id = :c AND user_id = :me AND id = :id"
)

_SAME_WORD = (
    "SELECT id, word, meaning, as_read, source, created_at, updated_at, word_fold, meaning_fold"
    " FROM user_words WHERE customer_id = :c AND user_id = :me AND word_fold = :wf"
    " ORDER BY created_at, id"
)

_COUNT = "SELECT count(*) FROM user_words WHERE customer_id = :c AND user_id = :me"

_INSERT = (
    "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold, as_read, source)"
    " VALUES (:c, :me, :w, :wf, :m, :mf, :as_read, :source)"
    " ON CONFLICT ON CONSTRAINT user_words_same_meaning DO NOTHING"
    " RETURNING id, word, meaning, as_read, source, created_at, updated_at, word_fold, meaning_fold"
)

_UPDATE = (
    "UPDATE user_words SET word = :w, word_fold = :wf, meaning = :m, meaning_fold = :mf"
    " WHERE customer_id = :c AND user_id = :me AND id = :id"
    " RETURNING id, word, meaning, as_read, source, created_at, updated_at, word_fold, meaning_fold"
)

_DELETE = "DELETE FROM user_words WHERE customer_id = :c AND user_id = :me AND id = :id RETURNING id"

_ADD_SPOT = (
    "INSERT INTO user_word_spots (customer_id, user_id, word_id, document_id, page_no, region)"
    " VALUES (:c, :me, :w, :d, :p, CAST(:r AS jsonb))"
    " ON CONFLICT ON CONSTRAINT user_word_spots_once DO NOTHING"
)

_SPOTS = (
    "SELECT s.id, s.word_id, w.word, w.meaning, s.page_no, s.region FROM user_word_spots s"
    " JOIN user_words w ON w.customer_id = s.customer_id AND w.user_id = s.user_id AND w.id = s.word_id"
    " WHERE s.customer_id = :c AND s.user_id = :me AND w.customer_id = :c AND w.user_id = :me"
    " AND s.document_id = :d AND s.page_no = :p"
    " ORDER BY s.created_at, w.word_fold, w.meaning_fold, s.id"
)


def list_words(conn: Connection, customer_id: str, user_id: str) -> list[WordRow]:
    """Every word of this user (at most MAX_WORDS), by word, then meaning.
    O(W) rows read through the unique index, O(W) space."""
    rows = conn.execute(text(_LIST), {"c": customer_id, "me": user_id, "limit": MAX_WORDS})
    return [_word(row) for row in rows]


def add_word(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    word: WordText,
    meaning: WordText,
    source: Literal["manual", "upload", "viewer"],
    confirm_another: bool = False,
    replace_id: str | None = None,
    as_read: str | None = None,
) -> Added | HasMeaning:
    """Add one word and meaning by the rules in the module docstring. Raises WordError.
    At most five indexed statements: O(log W + m) time for the word's m meanings
    (m is at most MAX_MEANINGS), O(m) space."""
    me = {"c": customer_id, "me": user_id}
    _lock_user(conn, me)
    if replace_id is not None:
        target = _get(conn, me, replace_id)
        if target.word_fold != word.fold:
            raise WordError("not_same_word")
        return Added(word=_update(conn, me, target, word, meaning), outcome="replaced")
    same = list(conn.execute(text(_SAME_WORD), {**me, "wf": word.fold}))
    for row in same:
        if row.meaning_fold == meaning.fold:
            return Added(word=_word(row), outcome="already")
    if len(same) >= MAX_MEANINGS:
        raise WordError("too_many_meanings")
    if int(conn.execute(text(_COUNT), me).scalar_one()) >= MAX_WORDS:
        raise WordError("too_many_words")
    if same and not confirm_another:
        return HasMeaning(existing=[_word(row) for row in same])
    values = {**me, **_texts(word, meaning), "as_read": as_read, "source": source}
    inserted = conn.execute(text(_INSERT), values).first()
    if inserted is None:  # a writer that skipped the user lock added it in the meantime
        raise WordError("conflict")
    return Added(word=_word(inserted), outcome="created")


def update_word(
    conn: Connection,
    customer_id: str,
    user_id: str,
    word_id: str,
    *,
    word: WordText | None = None,
    meaning: WordText | None = None,
    confirm_another: bool = False,
) -> WordRow | HasMeaning:
    """Change a word's text, its meaning, or both (None keeps it). Raises WordError:
    not_found; conflict when the result is another row of this user; too_many_meanings
    when it moves to a word that has MAX_MEANINGS already. Moving to a word that has
    other meanings asks first, as ``add_word`` does (``HasMeaning``, nothing written).
    Under the user lock, like ``add_word``. O(log W + m) time, O(m) space."""
    me = {"c": customer_id, "me": user_id}
    _lock_user(conn, me)
    current = _get(conn, me, word_id)
    new_word = word or WordText(text=current.word, fold=current.word_fold)
    new_meaning = meaning or WordText(current.meaning, current.meaning_fold)
    if new_word.fold != current.word_fold and not confirm_another:
        same = list(conn.execute(text(_SAME_WORD), {**me, "wf": new_word.fold}))
        if same and len(same) < MAX_MEANINGS and all(row.meaning_fold != new_meaning.fold for row in same):
            return HasMeaning(existing=[_word(row) for row in same])
    return _update(conn, me, current, new_word, new_meaning)


def delete_word(conn: Connection, customer_id: str, user_id: str, word_id: str) -> bool:
    """Delete one word of this user, and its spots with it (ON DELETE CASCADE).
    False if there is no such word. Under the user lock, so it waits for an add or a
    change of the same user to finish. O(log W + s) for its s spots."""
    params = {"c": customer_id, "me": user_id, "id": word_id}
    if conn.execute(text(_LOCK_USER), params).first() is None:
        return False  # not a user of this customer, so no word of theirs either
    return conn.execute(text(_DELETE), params).first() is not None


def add_spot(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    word_id: str,
    document_id: str,
    page_no: int | None = None,
    region: Mapping[str, Any] | None = None,
) -> None:
    """Link a word of this user to a document, or to a spot on one of its pages
    (``page_no`` and ``region`` together). The same link twice is kept once. The
    caller has checked the document is this customer's and the region's shape;
    the keys and CHECKs refuse anything else. O(log S)."""
    region_json = None if region is None else json.dumps(dict(region))
    params = {"c": customer_id, "me": user_id, "w": word_id, "d": document_id, "p": page_no, "r": region_json}
    conn.execute(text(_ADD_SPOT), params)


def spots_on_page(
    conn: Connection, customer_id: str, user_id: str, document_id: str, page_no: int
) -> list[SpotRow]:
    """This user's spots on one page of a document, with their word and meaning,
    oldest first. O(log S + k) for the page's k spots, O(k) space."""
    rows = conn.execute(text(_SPOTS), {"c": customer_id, "me": user_id, "d": document_id, "p": page_no})
    return [SpotRow(str(row[0]), str(row[1]), row[2], row[3], row[4], dict(row[5])) for row in rows]


def _lock_user(conn: Connection, me: Mapping[str, str]) -> None:
    if conn.execute(text(_LOCK_USER), me).first() is None:
        raise WordError("not_found")  # not a user of this customer


def _get(conn: Connection, me: Mapping[str, str], word_id: str) -> Any:
    row = conn.execute(text(_GET), {**me, "id": word_id}).first()
    if row is None:
        raise WordError("not_found")
    return row


def _update(
    conn: Connection, me: Mapping[str, str], current: Any, word: WordText, meaning: WordText
) -> WordRow:
    same = [row for row in conn.execute(text(_SAME_WORD), {**me, "wf": word.fold}) if row.id != current.id]
    if any(row.meaning_fold == meaning.fold for row in same):
        raise WordError("conflict")
    if word.fold != current.word_fold and len(same) >= MAX_MEANINGS:
        raise WordError("too_many_meanings")
    updated = conn.execute(text(_UPDATE), {**me, **_texts(word, meaning), "id": current.id}).first()
    if updated is None:  # gone since it was read: only a writer that skipped the user lock
        raise WordError("not_found")
    return _word(updated)


def _texts(word: WordText, meaning: WordText) -> dict[str, str]:
    return {"w": word.text, "wf": word.fold, "m": meaning.text, "mf": meaning.fold}


def _word(row: Any) -> WordRow:
    return WordRow(
        str(row.id), row.word, row.meaning, row.as_read, row.source, row.created_at, row.updated_at
    )
