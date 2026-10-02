"""Story of this PDF (0009): one user's own story about a document family.

Every function takes a connection from ``Database.user(customer_id, user_id)``
(the restrictive policy hides every row from any other transaction), and every
statement also filters on ``customer_id = :c AND user_id = :me``. A story is
found by any version's document id and stored on its family (the first
version's id), so every version shares it. The caller checks Trash.

* ``put_story`` creates or replaces the story in one statement. An edit clears
  the vector and its tag in that same statement, so a vector never describes
  older text; saving the same text keeps it.
* ``set_embedding`` writes a vector only while the story still has the hash of
  the text that was embedded (``story_hash``), so a slow embed that finishes
  after an edit is dropped, never stored as the new text's vector.

``check_story`` is pure. A story is never evidence and is never logged.
"""

import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.chunking.script import latin_spelling
from kaagaz.search.hybrid import vector_literal

STORY_MAX = 1000
VECTOR_DIM = 1024
# The input recipe of a story's vector (embedding_model is "{MODEL_EMBED}#story1").
RECIPE = "story1"
# The zero-width joiners Devanagari spelling needs; every other format character
# (Cf, which holds every direction control) is removed.
_JOINERS = frozenset("\u200c\u200d")
_REMOVED = ("Cc", "Cf", "Cs")


class StoryTextError(ValueError):
    """A story that is refused. ``reason`` is one of: empty, too_long, no_letter."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class StoryText:
    """Checked text: the story as stored, and its Latin spelling."""

    text: str
    latin: str


@dataclass(frozen=True, slots=True)
class StoryRow:
    family_id: str
    story: str
    embedding_model: str | None
    created_at: datetime
    updated_at: datetime


def check_story(value: str) -> StoryText:
    """The story as stored: control (Cc), format (Cf) other than the joiners and
    surrogate characters removed, Unicode NFC, white space runs made one space,
    trimmed; 1 to STORY_MAX characters with a letter or digit. Raises
    StoryTextError. O(n) in the length."""
    kept = "".join(ch for ch in value if _kept(ch))
    story = " ".join(unicodedata.normalize("NFC", kept).split())
    if not story:
        raise StoryTextError("empty")
    if len(story) > STORY_MAX:
        raise StoryTextError("too_long")
    if not any(unicodedata.category(ch)[0] in "LN" for ch in story):
        raise StoryTextError("no_letter")
    return StoryText(text=story, latin=latin_spelling(story))


def _kept(ch: str) -> bool:
    return ch.isspace() or ch in _JOINERS or unicodedata.category(ch) not in _REMOVED


def story_hash(story: str) -> str:
    """SHA-256 of the story's UTF-8, lower-case hex: what ``set_embedding`` checks."""
    return hashlib.sha256(story.encode()).hexdigest()


def recipe_tag(model_embed: str) -> str:
    """The ``embedding_model`` value of a story vector made by ``model_embed``."""
    return f"{model_embed}#{RECIPE}"


_GET = (
    "SELECT s.family_id, s.story, s.embedding_model, s.created_at, s.updated_at"
    " FROM document_stories s"
    " JOIN documents d ON d.customer_id = s.customer_id AND d.family_id = s.family_id"
    " WHERE s.customer_id = :c AND s.user_id = :me AND d.id = :d"
)

# One statement: the family from the document row, then insert or replace. The
# vector stays only when the text is the same.
_PUT = (
    "INSERT INTO document_stories AS s (customer_id, user_id, family_id, story, story_latin)"
    " SELECT :c, :me, d.family_id, :story, :latin FROM documents d WHERE d.customer_id = :c AND d.id = :d"
    " ON CONFLICT (customer_id, user_id, family_id) DO UPDATE SET"
    " story = EXCLUDED.story, story_latin = EXCLUDED.story_latin,"
    " embedding = CASE WHEN s.story = EXCLUDED.story THEN s.embedding END,"
    " embedding_model = CASE WHEN s.story = EXCLUDED.story THEN s.embedding_model END"
    " RETURNING s.family_id, s.story, s.embedding_model, s.created_at, s.updated_at"
)

_DELETE = (
    "DELETE FROM document_stories s USING documents d"
    " WHERE d.customer_id = :c AND d.id = :d"
    " AND s.customer_id = :c AND s.user_id = :me AND s.family_id = d.family_id"
    " RETURNING s.family_id"
)

_SET_EMBEDDING = (
    "UPDATE document_stories SET embedding = CAST(:v AS halfvec(1024)), embedding_model = :m"
    " WHERE customer_id = :c AND user_id = :me AND family_id = :f"
    " AND encode(sha256(convert_to(story, 'UTF8')), 'hex') = :h"
    " RETURNING family_id"
)


def get_story(conn: Connection, customer_id: str, user_id: str, document_id: str) -> StoryRow | None:
    """This user's story of ``document_id``'s family, or None. O(log D + log S)."""
    row = conn.execute(text(_GET), {"c": customer_id, "me": user_id, "d": document_id}).first()
    return None if row is None else _story(row)


def put_story(
    conn: Connection, customer_id: str, user_id: str, document_id: str, story: StoryText
) -> StoryRow | None:
    """Create or replace this user's story of ``document_id``'s family (rules in the
    module docstring). None if there is no such document for this customer.
    One statement, O(log D + log S)."""
    params = {"c": customer_id, "me": user_id, "d": document_id, "story": story.text, "latin": story.latin}
    row = conn.execute(text(_PUT), params).first()
    return None if row is None else _story(row)


def delete_story(conn: Connection, customer_id: str, user_id: str, document_id: str) -> bool:
    """Delete this user's story of ``document_id``'s family. False if there was none."""
    params = {"c": customer_id, "me": user_id, "d": document_id}
    return conn.execute(text(_DELETE), params).first() is not None


def set_embedding(
    conn: Connection,
    customer_id: str,
    user_id: str,
    family_id: str,
    *,
    expected_hash: str,
    vector: Sequence[float],
    model_embed: str,
) -> bool:
    """Store the story's vector, tagged ``recipe_tag(model_embed)``, only if the story
    still hashes to ``expected_hash``. False (nothing written) when it changed or is
    gone. Raises ValueError for a vector of the wrong size or a non-finite number.
    O(log S + VECTOR_DIM)."""
    if len(vector) != VECTOR_DIM:
        raise ValueError(f"a story vector holds {VECTOR_DIM} numbers")
    if not model_embed:
        raise ValueError("model_embed is empty")
    params = {
        "c": customer_id,
        "me": user_id,
        "f": family_id,
        "h": expected_hash,
        "v": vector_literal(vector),
        "m": recipe_tag(model_embed),
    }
    return conn.execute(text(_SET_EMBEDDING), params).first() is not None


def _story(row: Any) -> StoryRow:
    return StoryRow(str(row.family_id), row.story, row.embedding_model, row.created_at, row.updated_at)
