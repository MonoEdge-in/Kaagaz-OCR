"""Meaning vectors for stories (Story of this PDF, S2), made with the pieces' model.

A story is embedded as its own text, with no instruction (a story is matched
like a document, the model card's way), and its vector is tagged
``{MODEL_EMBED}#story1`` (``document_stories.recipe_tag``).

* ``embed_story``: the PUT's inline embed, right after the save has committed.
* ``embed_missing_stories``: one pass over the stories with no vector (the
  worker, every 10 minutes) or with no current vector (``reembed``). The ids
  come from the owner-rights function ``kaagaz_stories_to_embed`` (0010),
  which gives ids only, in key order after a cursor; each story is then read
  and written as its own user (``Database.user``), because only its author's
  transactions see it. The pass returns the cursor for the next one, so a
  story whose vector is refused is passed over, never tried forever.

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
from kaagaz.db.customers import is_active
from kaagaz.db.document_stories import get_story, recipe_tag, set_embedding, story_hash
from kaagaz.db.engine import Database, unavailable

logger = logging.getLogger(__name__)

# Through the one owner-rights function (0010): ids only, never the text.
_TO_EMBED = text(
    "SELECT s.customer_id, s.user_id, s.family_id"
    " FROM kaagaz_stories_to_embed(:tag, :c, :n, :after_c, :after_u, :after_f) AS s"
)

StoryKey = tuple[str, str, str]  # (customer, user, family): a story's key
# The most ids the function gives at once (0010's LIMIT_MOST).
PASS_MOST = 100


@dataclass(frozen=True, slots=True)
class StoryPass:
    """One pass: how many stories it found to embed, how many vectors it wrote (fewer
    when a story was edited, deleted or refused meanwhile), and the cursor for the
    next pass: the last key when the pass was full, None when it reached the end."""

    found: int
    written: int
    after: StoryKey | None = None


def stories_to_embed(
    conn: Connection,
    *,
    limit: int,
    tag: str | None = None,
    customer_id: str | None = None,
    after: StoryKey | None = None,
) -> list[StoryKey]:
    """(customer, user, family) of at most ``limit`` stories (the function caps it at
    100) with no vector, or with no vector tagged ``tag`` when it is given, in key
    order after ``after``. ``conn`` comes from ``Database.system()``. One scan of
    document_stories: O(S) for S stories."""
    cursor = after or (None, None, None)
    params = {"tag": tag, "c": customer_id, "n": limit, "after_c": cursor[0], "after_u": cursor[1]}
    rows = conn.execute(_TO_EMBED, {**params, "after_f": cursor[2]})
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
    db: Database,
    model: ModelClient,
    *,
    limit: int,
    tag: str | None = None,
    customer_id: str | None = None,
    after: StoryKey | None = None,
) -> StoryPass:
    """Embed at most ``limit`` stories from ``stories_to_embed`` (after ``after``) in
    one model call. A story deleted meanwhile, or embedded with today's tag
    meanwhile (its PUT), is skipped, and so is one whose customer is not active
    (0014): it costs no model call, and the cursor moves past it all the same.
    1 + 2k short transactions for the k stories found (each read also looks up
    its customer's status); O(k x 1024) memory."""
    with unavailable(), db.system() as conn:
        ids = stories_to_embed(conn, limit=limit, tag=tag, customer_id=customer_id, after=after)
    current = recipe_tag(model.embed_model)
    stories: list[tuple[str, str, str, str]] = []
    for customer, user, family in ids:
        with unavailable(), db.user(customer, user) as conn:
            # The family id is its first version's.
            row = get_story(conn, customer, user, family) if is_active(conn, customer) else None
        if row is not None and row.embedding_model != current:
            stories.append((customer, user, family, row.story))
    following = ids[-1] if ids and len(ids) >= min(limit, PASS_MOST) else None
    if not stories:
        return StoryPass(found=len(ids), written=0, after=following)
    vectors = model.embed([story[3] for story in stories])
    if len(vectors) != len(stories):
        raise ModelOutputError("embedding_count")
    written = sum(_write(db, model, story, vector) for story, vector in zip(stories, vectors, strict=True))
    return StoryPass(found=len(ids), written=written, after=following)
