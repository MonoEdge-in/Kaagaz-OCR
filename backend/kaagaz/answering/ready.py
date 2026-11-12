"""Ready answers for Home's questions (Tushar, 24 Oct 2026: "keep their response in memory so
the response is within 0.5 s, not all questions; change them as per the user's question
habits"). Home shows up to ``MAX_QUESTIONS`` questions for each person: the ones they ask most
(first questions of a conversation about all their documents that were answered, last
``HABIT_DAYS`` days), then ones that suit the kinds of documents their organisation holds most.
Home offers a question only once its answer is ready (Tushar, 24 Oct 2026: "show the question
when the answer is ready; no new questions until the old one is used, and 24 hours"): the list
stays until the person uses one of them or ``SHOWN_SECONDS`` pass. Answers are made in the
background and kept in this process's memory; Ask uses one only for that exact question,
asked fresh (no conversation, no picked documents, no clarify), by the same person, with the
same answer settings, switches and marks, while their documents are unchanged (``stamp``).
Every other question is answered as before.

Repeated questions (#535, ``repeat_key``): any fresh question (no conversation, no clarify), in any
scope, asked again by the same person with the same answer settings, switches and marks, the same
day, on the same running code and answer-shaping settings, while the stamp, a tag scope's
documents and the words that steer the search are unchanged, is answered from its stored answer
message (routers/chat.py), with no model call. In the database, so every API process finds it;
under RLS, the asker's organisation's messages only. Only a found answer (answered or unchecked)
the model drafted (or a fixed one), never one made while the model or the question's vector was
down, and never once any answer with its key is rated Not useful.
shortcut: found by a scan of the organisation's messages of the last day (the
(customer_id, created_at) index); a jsonb index on the key if a day's messages grow large.

shortcut: one API process (docker-compose runs one uvicorn), so a per-process cache is the
whole cache; with several API processes each would warm its own, so share it (Postgres or
Redis) before adding processes. A value changed in place without its ``updated_at`` moving
is caught only by ``TTL_SECONDS``; a document or value added, removed or re-read changes the stamp
at once.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, text

MAX_QUESTIONS = 3
HABIT_DAYS = 60
TTL_SECONDS = 24 * 3600  # a ready answer is made again after a day, or at once when documents change
SHOWN_SECONDS = 24 * 3600  # Home's list stays a day at most, and until one of it is used
MAX_ENTRIES = 512  # about 3 per person: plenty of people before the oldest are dropped

# Questions about what documents say, for the kinds a library holds most of (no AI makes them).
# Ask reads what documents say, never when they came, so nothing about uploads is offered.
TYPE_QUESTIONS: Mapping[str, str] = {
    "invoice": "Which invoice had the largest total last month?",
    "bank_statement": "What were the bank charges last month?",
    "contract": "Which agreements end in the next three months?",
    "land_record": "Who are the owners named in the land records?",
    "receipt": "How much did we pay to each party this month?",
    "register": "Which register entry has the largest amount?",
}

# This person's first questions of a conversation that were answered, most asked first.
_HABITS = text(
    "SELECT min(q.text) AS question FROM conversations c"
    " JOIN messages q ON q.customer_id = c.customer_id AND q.conversation_id = c.id AND q.role = 'user'"
    " JOIN messages a ON a.customer_id = q.customer_id AND a.conversation_id = q.conversation_id"
    " AND a.role = 'assistant' AND a.created_at = q.created_at"
    " WHERE c.customer_id = :c AND c.user_id = :u"
    " AND q.created_at > now() - make_interval(days => :days)"
    " AND a.answer ->> 'status' IN ('answered', 'unchecked')"
    " AND char_length(q.text) BETWEEN 8 AND 200 AND q.scope IS NULL"  # about all documents, never one
    " AND coalesce(a.answer ->> 'route', '') <> 'small_talk'"  # never "hi" or "what can you do"
    " AND NOT EXISTS (SELECT 1 FROM messages p WHERE p.customer_id = q.customer_id"
    " AND p.conversation_id = q.conversation_id AND p.created_at < q.created_at)"
    " GROUP BY lower(regexp_replace(btrim(q.text), '\\s+', ' ', 'g'))"
    " ORDER BY count(*) DESC, max(q.created_at) DESC LIMIT :n"
)
_TYPES = text(
    "SELECT doc_type FROM documents WHERE customer_id = :c AND is_current AND deleted_at IS NULL"
    " AND doc_type IS NOT NULL GROUP BY doc_type ORDER BY count(*) DESC, doc_type"
)
# What a ready answer was made from: the documents (how many, how many out of Trash, A10: a change
# Trash makes is never hidden by a newer time elsewhere) and their values (how many, newest change),
# and the word and line changes that change text (#527: a correction may change only a search
# piece; its Undo only ``undone_at``; a Confirm or Ensemble's resolve changes nothing), and the
# pieces still without a vector and the vectors' recipes (#535: a vector filled in by the worker
# later, or cleared by a line correction, changes what meaning search finds). A document not read
# yet (received or clean) is left out: its progress writes move its updated_at page by page, and
# each move made Home pay for its answers again (production QA B2).
# shortcut: three scans of one customer's rows, fine at today's thousands; an index on
# (customer_id, updated_at) or a change counter when a customer holds millions.
_STAMP = text(
    "SELECT (SELECT count(*) || '/' || count(*) FILTER (WHERE deleted_at IS NULL) || '/'"
    " || coalesce(max(updated_at)::text, '') FROM documents WHERE customer_id = :c"
    " AND status NOT IN ('received', 'clean')) || '|' || (SELECT"
    " count(*) || '/' || coalesce(max(updated_at)::text, '') FROM extracted_values WHERE customer_id = :c)"
    " || '|' || (SELECT count(*) || '/' || coalesce(max(greatest(created_at, undone_at))::text, '')"
    " FROM word_changes WHERE customer_id = :c"
    " AND before_text <> after_text) || '|' || (SELECT count(*) FILTER (WHERE embedding IS NULL) || '/'"
    " || coalesce(string_agg(DISTINCT embedding_model, ','), '') FROM pieces WHERE customer_id = :c)"
)
# #535: the documents a tag scope holds, with their tags, so a tag put on or taken off moves the key.
_TAGGED = text(
    "SELECT document_id::text || ':' || tag_id::text FROM document_tags"
    " WHERE customer_id = :c AND tag_id = ANY(CAST(:t AS uuid[])) ORDER BY 1"
)
# #535: the settings that shape an answer (its evidence, window, models and parts), so a change of
# one on the same commit makes a repeated question's key another.
ANSWER_SHAPING = (
    "model_enabled",
    "model_chat",
    "model_embed",
    "model_answer_max_tokens",
    "model_context_tokens",
    "search_ef",
    "answer_max_evidence",
    "answer_min_evidence_score",
    "answer_neighbours",
    "answer_translate_question",
    "answer_whole_doc_tokens",
    "answer_swift_input_tokens",
    "answer_parts_tokens",
    "answer_parts_max",
    "topic_expand_max_pieces",
    "query_aspects_max",
    "chat_history_turns",
    "timezone",
)


# Repeated questions (#535): the words that steer a search besides the documents, the asker's own
# (My words) and the organisation's approved ones, exactly. ``Database.user``: the asker's own rows.
_WORDS = text(
    "SELECT coalesce((SELECT md5(jsonb_agg(jsonb_build_array(word, meaning) ORDER BY word, meaning)::text)"
    " FROM user_words WHERE customer_id = :c AND user_id = :u), '') || '|' || coalesce((SELECT"
    " md5(jsonb_agg(jsonb_build_array(word, meaning, document_id) ORDER BY word, meaning, document_id)::text)"
    " FROM organization_word_suggestions WHERE customer_id = :c AND status = 'approved'), '')"
)
REPEAT_KEY = "repeat_key"  # the stored answer's field that holds it; never shown (clarify.without_resume)
REPEAT_COPY = "repeat_copy"  # a copy served from it: never served itself, its Not useful still counts


def same_question(question: str) -> str:
    """The form two askings of one question share: case and spacing aside."""
    return " ".join(question.split()).casefold()


def questions(conn: Connection, customer_id: str, user_id: str) -> list[tuple[str, bool]]:
    """Up to MAX_QUESTIONS (question, theirs): this person's habits first, then their
    library's kinds. Two statements."""
    params = {"c": customer_id, "u": user_id, "days": HABIT_DAYS, "n": MAX_QUESTIONS}
    picked: list[tuple[str, bool]] = [(str(row[0]), True) for row in conn.execute(_HABITS, params)]
    seen = {same_question(question) for question, _ in picked}
    for row in conn.execute(_TYPES, {"c": customer_id}):
        suggestion = TYPE_QUESTIONS.get(str(row[0]))
        if suggestion and len(picked) < MAX_QUESTIONS and same_question(suggestion) not in seen:
            picked.append((suggestion, False))
            seen.add(same_question(suggestion))
    return picked


def stamp(conn: Connection, customer_id: str) -> str:
    """The documents and values a ready answer was made from; it changes when they do."""
    return str(conn.execute(_STAMP, {"c": customer_id}).scalar_one())


def key(
    customer_id: str, user_id: str, question: str, inputs: Sequence[Any], made_from: str
) -> tuple[str, ...]:
    """Everything an answer depends on besides the question: ``inputs`` are the answer
    settings, switches and marks (their reprs are exact), ``made_from`` the ``stamp``."""
    return (customer_id, user_id, same_question(question), *map(repr, inputs), made_from)


def tagged(conn: Connection, customer_id: str, tag_ids: Sequence[str] | None) -> list[str]:
    """#535: "document:tag" for each document holding one of ``tag_ids``, sorted; [] for none. One
    statement, only with a tag scope."""
    if not tag_ids:
        return []
    return [str(row[0]) for row in conn.execute(_TAGGED, {"c": customer_id, "t": list(tag_ids)})]


def shaping(settings: Any) -> tuple[Any, ...]:
    """#535: the ``ANSWER_SHAPING`` settings' values, in order."""
    return tuple(getattr(settings, name) for name in ANSWER_SHAPING)


def repeat_key(
    conn: Connection, customer_id: str, user_id: str, question: str, inputs: Sequence[Any], made_from: str
) -> str:
    """#535: ``key`` for a repeated question, as a sha256 to store with its answer: ``inputs`` (the
    caller's: the answer settings, switches, marks, scope and its tagged documents, day, running
    code and ``shaping``), the ``stamp`` (``made_from``, read once by the caller) and the words that
    steer the search (``_WORDS``). On a ``Database.user`` connection. One statement."""
    words = str(conn.execute(_WORDS, {"c": customer_id, "u": user_id}).scalar_one())
    parts = key(customer_id, user_id, question, inputs, f"{made_from}|{words}")
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class ReadyAnswers:
    """Answers kept for a while, the oldest dropped past MAX_ENTRIES; thread-safe (Ask runs
    in a thread pool). ``claim`` keeps two Home loads from making one answer twice."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: OrderedDict[tuple[str, ...], tuple[float, Any]] = OrderedDict()
        self._making: set[tuple[str, ...]] = set()

    def get(self, at: tuple[str, ...]) -> Any | None:
        with self._lock:
            found = self._kept.get(at)
            if found is None:
                return None
            if self._clock() - found[0] > TTL_SECONDS:
                del self._kept[at]
                return None
            self._kept.move_to_end(at)
            return found[1]

    def claim(self, at: tuple[str, ...]) -> bool:
        """True for the one caller that should make this answer now."""
        with self._lock:
            if at in self._making or (at in self._kept and self._clock() - self._kept[at][0] <= TTL_SECONDS):
                return False
            self._making.add(at)
            return True

    def put(self, at: tuple[str, ...], answer: Any) -> None:
        with self._lock:
            self._making.discard(at)
            self._kept[at] = (self._clock(), answer)
            self._kept.move_to_end(at)
            while len(self._kept) > MAX_ENTRIES:
                self._kept.popitem(last=False)

    def release(self, at: tuple[str, ...]) -> None:
        """Making it failed: let a later Home load try again."""
        with self._lock:
            self._making.discard(at)


class ShownQuestions:
    """Each person's questions as Home last showed them (Tushar, 24 Oct 2026: "update them only
    after the user uses one"): the same list on every visit until they ask one of them fresh,
    then made again from their habits. An empty list is never kept, so questions come as soon
    as documents do. Oldest people dropped past MAX_ENTRIES; thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: OrderedDict[tuple[str, str], tuple[float, list[tuple[str, bool]]]] = OrderedDict()

    def get(self, customer_id: str, user_id: str) -> list[tuple[str, bool]] | None:
        """The list Home last showed this person, while it is less than SHOWN_SECONDS old."""
        with self._lock:
            found = self._kept.get((customer_id, user_id))
            if found is None:
                return None
            if self._clock() - found[0] > SHOWN_SECONDS:
                del self._kept[(customer_id, user_id)]
                return None
            self._kept.move_to_end((customer_id, user_id))
            return found[1]

    def keep(self, customer_id: str, user_id: str, shown: list[tuple[str, bool]]) -> None:
        """Keep a new list (its day starts now); the same list again keeps its first time."""
        if not shown:
            return
        with self._lock:
            found = self._kept.get((customer_id, user_id))
            since = found[0] if found is not None and found[1] == shown else self._clock()
            self._kept[(customer_id, user_id)] = (since, shown)
            self._kept.move_to_end((customer_id, user_id))
            while len(self._kept) > MAX_ENTRIES:
                self._kept.popitem(last=False)

    def used(self, customer_id: str, user_id: str, question: str) -> None:
        """They asked ``question``: when it is one Home showed them, the list is made again."""
        asked = same_question(question)
        with self._lock:
            found = self._kept.get((customer_id, user_id))
            if found is not None and any(same_question(text) == asked for text, _ in found[1]):
                del self._kept[(customer_id, user_id)]


READY = ReadyAnswers()
SHOWN = ShownQuestions()
