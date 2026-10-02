"""Meaning vectors for stories (Story of this PDF, S2), made with the pieces' model.

A story is embedded as its own text, with no instruction (a story is matched
like a document, the model card's way), and its vector is tagged
``{MODEL_EMBED}#story1`` (``document_stories.recipe_tag``).

* ``embed_story``: the PUT's inline embed, right after the save has committed.
* ``embed_missing_stories``: one pass over the stories with no vector (the
  worker, every 10 minutes) or with no current vector (``reembed``). The ids
  come from the owner-rights function ``kaagaz_stories_to_embed`` (0010),
  which gives ids only; each story is then read and written as its own user
  (``Database.user``), because only its author's transactions see it.

Every write goes through ``set_embedding``, which stores the vector only while
the story still hashes to the text that was embedded, so a story edited while
the model worked never gets a vector of its older text. The model is called
outside any transaction. A vector ``set_embedding`` refuses (a number that is
not finite or too large for halfvec) is not written: the story stays without
one, and only the code is logged. ``ModelUnavailable`` and ``ModelOutputError``
propagate, and so does ``BackendUnavailable("database")``: the caller decides.
No story text is logged or leaves through an error.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, text

from kaagaz.ai.client import ModelClient, ModelOutputError
from kaagaz.db.document_stories import get_story, set_embedding, story_hash
from kaagaz.db.engine import Database, unavailable

logger = logging.getLogger(__name__)

# Through the one owner-rights function (0010): ids only, never the text.
_TO_EMBED = text(
    "SELECT s.customer_id, s.user_id, s.family_id FROM kaagaz_stories_to_embed(:tag, :c, :n) AS s"
)


@dataclass(frozen=True, slots=True)
class StoryPass:
    """One pass: how many stories it found to embed, and how many vectors it wrote
    (fewer when a story was edited or deleted meanwhile)."""

    found: int
    written: int


def stories_to_embed(
    conn: Connection, *, limit: int, tag: str | None = None, customer_id: str | None = None
) -> Sequence[tuple[str, str, str]]:
    """(customer, user, family) of at most ``limit`` stories with no vector, or with
    no vector tagged ``tag`` when it is given; oldest change first. ``conn`` comes
    from ``Database.system()``. One scan of document_stories: O(S) for S stories."""
    rows = conn.execute(_TO_EMBED, {"tag": tag, "c": customer_id, "n": limit})
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _write(
    db: Database, model: ModelClient, story: tuple[str, str, str, str], vector: Sequence[float]
) -> bool:
    """``set_embedding`` as the story's own user. False when nothing was written."""
    customer_id, user_id, family_id, words = story
    try:
        with unavailable(), db.user(customer_id, user_id) as conn:
            return set_embedding(
                conn,
                customer_id,
                user_id,
                family_id,
                expected_hash=story_hash(words),
                vector=vector,
                model_embed=model.embed_model,
            )
    except ValueError:  # the wrong size, or a number halfvec can't hold
        logger.warning("story vector refused", extra={"code": "embedding_values"})
        return False


def embed_story(
    db: Database, model: ModelClient, customer_id: str, user_id: str, family_id: str, story: str
) -> bool:
    """Embed ``story`` (the text just saved) and write its vector. False when the
    story changed or went meanwhile, or the vector was refused (nothing written).
    One model call, one short transaction."""
    vectors = model.embed([story])
    if len(vectors) != 1:
        raise ModelOutputError("embedding_count")
    return _write(db, model, (customer_id, user_id, family_id, story), vectors[0])


def embed_missing_stories(
    db: Database, model: ModelClient, *, limit: int, tag: str | None = None, customer_id: str | None = None
) -> StoryPass:
    """Embed at most ``limit`` stories from ``stories_to_embed`` in one model call.
    1 + 2k short transactions for the k stories found; O(k x 1024) memory."""
    with unavailable(), db.system() as conn:
        ids = stories_to_embed(conn, limit=limit, tag=tag, customer_id=customer_id)
    stories: list[tuple[str, str, str, str]] = []
    for customer, user, family in ids:
        with unavailable(), db.user(customer, user) as conn:
            row = get_story(conn, customer, user, family)  # the family id is its first version's
        if row is not None:  # deleted meanwhile: nothing to embed
            stories.append((customer, user, family, row.story))
    if not stories:
        return StoryPass(found=len(ids), written=0)
    vectors = model.embed([story[3] for story in stories])
    if len(vectors) != len(stories):
        raise ModelOutputError("embedding_count")
    written = sum(_write(db, model, story, vector) for story, vector in zip(stories, vectors, strict=True))
    return StoryPass(found=len(ids), written=written)
