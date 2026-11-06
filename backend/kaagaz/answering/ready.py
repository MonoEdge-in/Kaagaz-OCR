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

shortcut: one API process (docker-compose runs one uvicorn), so a per-process cache is the
whole cache; with several API processes each would warm its own, so share it (Postgres or
Redis) before adding processes. A value changed in place without its ``updated_at`` moving
is caught only by ``TTL_SECONDS``; a document or value added, removed or re-read changes the stamp
at once.
"""

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
# What a ready answer was made from: the documents and their values (how many, newest change),
# and the word and line changes that change text (#527: a correction may change only a search
# piece; its Undo only ``undone_at``; a Confirm or Ensemble's resolve changes nothing).
# shortcut: two scans of one customer's rows, fine at today's thousands; an index on
# (customer_id, updated_at) or a change counter when a customer holds millions.
_STAMP = text(
    "SELECT (SELECT count(*) || '/' || coalesce(max(updated_at)::text, '') FROM documents"
    " WHERE customer_id = :c) || '|' || (SELECT count(*) || '/' || coalesce(max(updated_at)::text, '')"
    " FROM extracted_values WHERE customer_id = :c) || '|' || (SELECT count(*) || '/'"
    " || coalesce(max(greatest(created_at, undone_at))::text, '') FROM word_changes WHERE customer_id = :c"
    " AND before_text <> after_text)"
)


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
