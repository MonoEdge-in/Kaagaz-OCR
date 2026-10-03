"""Meaning vectors for stories (Story of this PDF, S2), made with the pieces' model.

A story is embedded as its own text, with no instruction (a story is matched
like a document, the model card's way), and its vector is tagged
``{MODEL_EMBED}#story1`` (``document_stories.recipe_tag``). ``embed_story`` is
the PUT's inline embed, right after the save has committed.

The write goes through ``set_embedding``, which stores the vector only while
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

from kaagaz.ai.client import ModelClient, ModelOutputError
from kaagaz.db.document_stories import set_embedding, story_hash
from kaagaz.db.engine import Database, unavailable

logger = logging.getLogger(__name__)


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
