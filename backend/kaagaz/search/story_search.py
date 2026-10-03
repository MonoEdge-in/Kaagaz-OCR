"""Story matches over the signed-in user's own stories (Story of this PDF, S3, PROVISIONAL).

``search_stories`` reads them in one statement (``read_stories``) in a short
``Database.user`` transaction, then ranks them after it (``story_match``), and
says how many it read (``searched``) and whether that was every story (``all``:
false past ``SCAN_MOST``); ``find_story_matches`` is its side-card form. The
RESTRICTIVE ``own_user_only`` policy hides every other user's story, and the
statement names the customer and user too (two locks). Only a family whose current
version is ready, out of Trash (``LIVE_CURRENT``) and in the scope counts; the match
points at that version. The cosine is computed only while ``MEANING_THRESHOLDS``
holds today's recipe (OFF: uncalibrated).

Never evidence: nothing that answers imports this (tests/test_story_search_guard.py).
The question and the stories are never logged; a match holds ids, a name and a band.

ponytail: an exact scan of the user's ``SCAN_MOST`` most recently changed stories
(written by hand; no index, council). Upgrade: a word or trigram prefilter in SQL.
Time: O(S log S) for S stories in SQL, then ``rank``'s O(T + V * W + S log S).
Space: the S <= SCAN_MOST + 1 rows read, O(T).
"""

import dataclasses
import math
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db.document_stories import recipe_tag
from kaagaz.db.engine import Database
from kaagaz.search import story_match
from kaagaz.search.hybrid import vector_literal
from kaagaz.search.scope import EVERYTHING, Scope
from kaagaz.search.story_match import Candidate, Mode, StoryMatch

SCAN_MOST = 500  # stories read per request, most recently changed first (PROVISIONAL)

# The family's current version, ready, out of Trash (LIVE_CURRENT, written out), in the
# scope (hybrid.py's predicates, with d.id for a chosen document). :recipe is NULL when
# meaning is off, so no cosine is computed.
_STORIES = text(
    "SELECT d.id, d.display_name, d.doc_type, s.story_latin,"
    " CASE WHEN s.embedding_model = CAST(:recipe AS text)"
    " THEN 1 - (s.embedding <=> CAST(:vec AS halfvec(1024))) END AS meaning"
    " FROM document_stories s"
    " JOIN documents d ON d.customer_id = s.customer_id AND d.family_id = s.family_id"
    " WHERE s.customer_id = :c AND s.user_id = :me"
    " AND d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    " ORDER BY s.updated_at DESC, d.id LIMIT :n"
)


def meaning_threshold(model_embed: str) -> float | None:
    """The calibrated threshold of today's story recipe, None while uncalibrated (OFF)."""
    return story_match.MEANING_THRESHOLDS.get(recipe_tag(model_embed))


def read_stories(
    conn: Connection,
    *,
    customer_id: str,
    user_id: str,
    recipe: str | None,
    embedding: list[float] | None,
    scope: Scope = EVERYTHING,
    most: int = SCAN_MOST,
) -> list[Candidate]:
    """The user's stories that count, newest change first, at most ``most``, with
    their cosine when ``recipe`` and ``embedding`` are given. ``conn`` comes from
    ``Database.user(customer_id, user_id)``. One statement."""
    params = {
        "c": customer_id,
        "me": user_id,
        "recipe": None if embedding is None else recipe,
        "vec": None if recipe is None or embedding is None else vector_literal(embedding),
        "n": most,
    }
    rows = conn.execute(_STORIES, params | scope.params()).all()
    # A zero vector has no direction: its NaN cosine says nothing.
    cosines = [None if row[4] is None or not math.isfinite(row[4]) else float(row[4]) for row in rows]
    return [Candidate(str(r[0]), str(r[1]), r[2], str(r[3]), m) for r, m in zip(rows, cosines, strict=True)]


@dataclasses.dataclass(frozen=True, slots=True)
class StorySearch:
    """The matches, how many stories were read, and whether that was all of them."""

    matches: list[StoryMatch]
    searched: int
    all: bool


def search_stories(
    db: Database,
    *,
    customer_id: str,
    user_id: str,
    question: str,
    embedding: list[float] | None,
    model_embed: str,
    scope: Scope = EVERYTHING,
    mode: Mode = "cards",
    most: int = story_match.MATCHES_MAX,
) -> StorySearch:
    """At most ``most`` of this user's documents whose story the question tells again,
    best first (story_match's rules). The rows are read in one short
    ``Database.user`` transaction and ranked after it has ended, so the matching's CPU
    never holds a connection. Nothing before any SQL for a scope that matches nothing,
    or a question with no content word while meaning is off."""
    if scope.matches_nothing:
        return StorySearch([], 0, True)
    words = story_match.question_words(question)
    threshold = meaning_threshold(model_embed) if embedding is not None else None
    if not words and threshold is None:
        return StorySearch([], 0, True)
    recipe = None if threshold is None else recipe_tag(model_embed)
    with db.user(customer_id, user_id) as conn:
        stories = read_stories(
            conn,
            customer_id=customer_id,
            user_id=user_id,
            recipe=recipe,
            embedding=embedding,
            scope=scope,
            most=SCAN_MOST + 1,  # one more says whether there were more
        )
    read = stories[:SCAN_MOST]
    found = story_match.rank(words, read, threshold=threshold, mode=mode, most=most)
    return StorySearch(found, len(read), len(stories) <= SCAN_MOST)


def find_story_matches(
    db: Database,
    *,
    customer_id: str,
    user_id: str,
    question: str,
    embedding: list[float] | None,
    model_embed: str,
    scope: Scope = EVERYTHING,
) -> list[StoryMatch]:
    """The side cards: at most ``MATCHES_MAX`` matches (``search_stories``)."""
    return search_stories(
        db,
        customer_id=customer_id,
        user_id=user_id,
        question=question,
        embedding=embedding,
        model_embed=model_embed,
        scope=scope,
    ).matches


def story_matches_json(found: list[StoryMatch]) -> list[dict[str, Any]]:
    """The API's ``story_matches``: ids, a name and a band each, never a score or the story."""
    return [dataclasses.asdict(match) for match in found]
