"""Clarifying questions (PRD 6 B1): the plan's names checked against the company's vocabulary,
``value_keys`` (GitHub #245): ``latin`` (``latin_spelling``, casefolded) and ``sound`` (sound
keys) per person, place or property value, read in one statement through ``LIVE_CURRENT`` with
the customer filter under row level security. A label (the value as printed) that is empty, over
``MAX_CHOICE_CHARS`` or reads like instructions is never a candidate.

Tiers, word by word (PROVISIONAL): match is the same spelling, a confirmed spelling (an approved
organisation word, decision 6; a "Did you mean" pick suggests one, B2) or the same sound key of
``SOUND_MATCH_MIN``+ letters ("Krishana" is Krishna, "पुणे" is Pune); close is the same first
letter and at most one edit
(Damerau-Levenshtein) for 4 to 6 letters, two for 7+, or a trigram similarity of
``CLOSE_SIMILARITY`` ("Puny" is close to Pune, "Tune" is not); ``EXACT_ONLY_MAX`` letters or fewer,
or a digit: as written only. One match resolves; several, or close only, ask (decision 13: one
question a turn, ``MAX_CHOICES`` and "Something else", ``MAX_TURNS`` turns, then an open list);
none is searched as typed. A missing subject is asked only when nothing narrows the question.
Places (B2) may be picked several at once, or "All of these"; "between A and B" offers the
company's places in A's and B's districts when known, else its newest (decision 11).
Time: one statement, O(V log V) over V live keys; then O(c * w * l^2) in Python.
"""

import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import RowMapping, text

from kaagaz.answering.evidence import looks_like_instructions
from kaagaz.answering.rewrite import MAX_ENTITY_CHARS, Plan
from kaagaz.answering.sanitize import clean_text
from kaagaz.chunking.script import latin_spelling, sound_key
from kaagaz.db.engine import Database
from kaagaz.extraction.links import normalise_identifier

_log = logging.getLogger(__name__)

Kind = Literal["person", "place", "property"]
Said = tuple[str, ...]  # assumption lines
MAX_CANDIDATES, MAX_CHOICES, MAX_CHOICE_CHARS, MAX_TURNS = 10, 6, 80, 2
SOUND_MATCH_MIN, EXACT_ONLY_MAX, CLOSE_SIMILARITY = 4, 3, 0.5
OTHER = {"id": "other", "label": "Something else", "kind": "other"}
MISSING: dict[Kind, str] = {"property": "Which property do you mean?", "person": "Which person do you mean?"}
MISSING["place"] = "Which places should I look in?"
ALL = {"id": "all", "label": "All of these", "kind": "all"}
_BETWEEN = re.compile(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?=[?.!,]|$)", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_LETTER = r"[\w\u0900-\u097F]"  # a letter, digit or Devanagari sign: a word goes on


@dataclass(frozen=True, slots=True)
class Entity:
    kind: Kind
    as_typed: str  # "": the newest entries of the kind


@dataclass(frozen=True, slots=True)
class Entry:
    """A distinct name of the vocabulary, as printed (``label``), and its documents."""

    kind: str
    latin: str
    label: str
    documents: tuple[str, ...]
    aliased: bool = False  # a confirmed spelling of the typed name


@dataclass(frozen=True, slots=True)
class Pending:
    """A stored clarify answered (``as_typed`` None: it asked for a missing subject)."""

    question: str
    kind: Kind
    as_typed: str | None
    remaining: tuple[Entity, ...]
    assumptions: tuple[str, ...]
    turn: int
    picked: tuple[str, ...]
    confirm: bool = False  # a "Did you mean" pick: its spelling is suggested to the company (B2)
    typed: str | None = None  # the words typed instead of a pick (B2)


@dataclass(frozen=True, slots=True)
class Step:
    """The question, names put in; the clarify to ask and its resume state (None: answer now)."""

    question: str
    assumptions: tuple[str, ...]
    clarify: dict[str, Any] | None = None
    resume: dict[str, Any] | None = None


class Vocabulary(Protocol):
    def candidates(self, customer_id: str, entities: Sequence[Entity]) -> list[list[Entry]]:
        """Each entity's best MAX_CANDIDATES entries of its kind, best (then newest) first."""
        ...

    def near(self, customer_id: str, districts: Sequence[str]) -> list[Entry]:
        """The newest places sharing a land record with one of ``districts`` (``latin_key``s)."""
        ...

    def within(self, documents: Sequence[str] | None) -> "Vocabulary":
        """The vocabulary of ``documents`` only (the chat's scope names them); None: every one."""
        ...

    def any_keys(self, customer_id: str) -> bool:
        """False only when there is no key at all, so ``candidates`` and ``near`` find nothing."""
        ...


_CANDIDATES = text(
    "WITH typed AS (SELECT * FROM unnest(CAST(:n AS int[]), CAST(:kinds AS text[]), CAST(:latins AS text[]),"
    " CAST(:sounds AS text[]), CAST(:folds AS text[])) AS t(n, kind, latin, sound, fold)),"
    " vocabulary AS MATERIALIZED (SELECT k.kind, k.latin, min(k.sound) AS sound, max(d.created_at) AS newest,"
    " (array_agg(v.value_text ORDER BY d.created_at DESC, v.id))[1] AS label,"
    " array_agg(DISTINCT k.document_id::text) AS documents FROM value_keys k"
    " JOIN documents d ON d.customer_id = k.customer_id AND d.id = k.document_id"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id"
    " WHERE k.customer_id = :c AND k.kind = ANY(CAST(:kinds AS text[]))"
    " AND (CAST(:scoped AS uuid[]) IS NULL OR k.document_id = ANY(CAST(:scoped AS uuid[])))"
    " GROUP BY k.kind, k.latin)"
    " SELECT t.n, w.kind, w.latin, w.label, w.documents, w.aliased FROM typed t CROSS JOIN LATERAL ("
    " SELECT x.*, EXISTS (SELECT 1 FROM organization_word_suggestions o WHERE o.customer_id = :c"
    " AND o.status = 'approved' AND o.document_id IS NULL AND o.word_fold = t.fold"
    " AND o.meaning_fold = x.latin) AS aliased FROM vocabulary x WHERE x.kind = t.kind"
    " ORDER BY x.latin = t.latin DESC, aliased DESC,"
    " string_to_array(x.latin, ' ') && string_to_array(t.latin, ' ') DESC,"
    " string_to_array(x.sound, ' ') && string_to_array(t.sound, ' ') DESC,"
    " word_similarity(t.latin, x.latin) DESC, x.newest DESC, x.latin LIMIT :most) w ORDER BY t.n"
)
_NEAR = text(
    "SELECT k.kind, k.latin, (array_agg(v.value_text ORDER BY d.created_at DESC, v.id))[1] AS label,"
    " array_agg(DISTINCT k.document_id::text) AS documents, false AS aliased FROM value_keys k"
    " JOIN documents d ON d.customer_id = k.customer_id AND d.id = k.document_id"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id"
    " WHERE k.customer_id = :c AND k.kind = 'place' AND k.place_level <> 'district'"
    " AND k.latin <> ALL(CAST(:districts AS text[]))"
    " AND (CAST(:scoped AS uuid[]) IS NULL OR k.document_id = ANY(CAST(:scoped AS uuid[]))) AND EXISTS ("
    " SELECT 1 FROM value_keys z JOIN extracted_values zv ON zv.customer_id = z.customer_id"
    " AND zv.id = z.value_id WHERE z.customer_id = :c AND z.document_id = k.document_id"
    " AND z.place_level = 'district' AND z.latin = ANY(CAST(:districts AS text[]))"
    " AND zv.record_key = v.record_key)"
    " GROUP BY k.kind, k.latin ORDER BY max(d.created_at) DESC, k.latin LIMIT :most"
)


# Ask speed (24 Oct 2026): whether the vocabulary has any key in scope, from value_keys_document.
_ANY_KEYS = text(
    "SELECT EXISTS (SELECT 1 FROM value_keys k WHERE k.customer_id = :c"
    " AND (CAST(:scoped AS uuid[]) IS NULL OR k.document_id = ANY(CAST(:scoped AS uuid[]))))"
)


class PgVocabulary:
    """The vocabulary in PostgreSQL: one statement in one ``Database.customer`` transaction."""

    def __init__(self, db: Database, documents: Sequence[str] | None = None) -> None:
        self._db, self._documents = db, None if documents is None else list(documents)

    def within(self, documents: Sequence[str] | None) -> "PgVocabulary":
        return PgVocabulary(self._db, documents)

    def candidates(self, customer_id: str, entities: Sequence[Entity]) -> list[list[Entry]]:
        found: list[list[Entry]] = [[] for _ in entities]
        latins = [latin_key(entity.as_typed, entity.kind) for entity in entities]
        sounds = [" ".join(map(sound_key, _WORD.findall(latin))) for latin in latins]
        kinds, most = [entity.kind for entity in entities], MAX_CANDIDATES
        params = {"c": customer_id, "n": list(range(len(entities))), "kinds": kinds, "most": most}
        params["scoped"] = self._documents
        folds = [" ".join(clean_text(entity.as_typed).casefold().split()) for entity in entities]
        params |= {"latins": latins, "sounds": sounds, "folds": folds}
        if entities:
            with self._db.customer(customer_id) as conn:
                for row in conn.execute(_CANDIDATES, params).mappings():
                    found[int(row["n"])].append(_entry(row))
        return found

    def any_keys(self, customer_id: str) -> bool:
        with self._db.customer(customer_id) as conn:
            return bool(conn.execute(_ANY_KEYS, {"c": customer_id, "scoped": self._documents}).scalar_one())

    def near(self, customer_id: str, districts: Sequence[str]) -> list[Entry]:
        with self._db.customer(customer_id) as conn:
            params = {"c": customer_id, "districts": list(districts), "most": MAX_CHOICES}
            rows = conn.execute(_NEAR, {**params, "scoped": self._documents})
            return [_entry(row) for row in rows.mappings()]


def _entry(row: RowMapping) -> Entry:
    return Entry(row["kind"], row["latin"], row["label"], tuple(sorted(row["documents"])), row["aliased"])


def latin_key(typed: str, kind: str = "person") -> str:
    """#245's ``latin`` of a typed name: ``latin_spelling``, casefolded, spaces collapsed; of a
    property, ``links.normalise_identifier``."""
    if kind == "property":
        return normalise_identifier(clean_text(typed))
    return " ".join(latin_spelling(clean_text(typed)).casefold().split())


def usable(entry: Entry) -> bool:
    label = clean_text(entry.label)
    return bool(label) and len(label) <= MAX_CHOICE_CHARS and not looks_like_instructions(label)


def distance(first: str, second: str, bound: int) -> int:
    """Damerau-Levenshtein (optimal string alignment), or ``bound + 1`` once over it. O(n * m)."""
    if abs(len(first) - len(second)) > bound:
        return bound + 1
    before: list[int] = []
    above = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        row = [i] + [0] * len(second)
        for j, b in enumerate(second, 1):
            row[j] = min(above[j] + 1, row[j - 1] + 1, above[j - 1] + (a != b))
            if before and j > 1 and a == second[j - 2] and first[i - 2] == b:
                row[j] = min(row[j], before[j - 2] + 1)
        if min(row) > bound:
            return bound + 1
        before, above = above, row
    return min(above[-1], bound + 1)


def _similarity(first: str, second: str) -> float:
    """pg_trgm's similarity of two words (story_match.py's; a story module is never imported)."""
    a, b = ({f"  {word} "[i : i + 3] for i in range(len(word) + 1)} for word in (first, second))
    return len(a & b) / len(a | b)


def _word_tier(word: str, words: Sequence[str]) -> str | None:
    key, close = sound_key(word) if word.isalpha() else "", False
    for other in words:
        if word == other or (len(key) >= SOUND_MATCH_MIN and key == sound_key(other)):
            return "match"
        if len(word) > EXACT_ONLY_MAX and word.isalpha() and word[0] == other[0]:
            bound = 1 if len(word) <= 6 else 2
            near = distance(word, other, bound) <= bound or _similarity(word, other) >= CLOSE_SIMILARITY
            close = close or near
    return "close" if close else None


def tier(typed: str, entry: Entry) -> str | None:
    """ "match", "close" or None for ``typed`` (a ``latin_key``) and ``entry``."""
    if entry.aliased or typed == entry.latin:
        return "match"
    tiers = [_word_tier(word, _WORD.findall(entry.latin)) for word in _WORD.findall(typed)]
    if not tiers or None in tiers:
        return None
    return "match" if set(tiers) == {"match"} else "close"


def outcome(entity: Entity, entries: Sequence[Entry]) -> tuple[str, tuple[Entry, ...]]:
    """("one" | "several" | "close" | "none", the entries) for ``entity``."""
    typed = latin_key(entity.as_typed, entity.kind)
    kept = [entry for entry in entries if usable(entry)]
    same = [entry for entry in kept if entry.aliased or entry.latin == typed]
    matched = same[:1] or [entry for entry in kept if tier(typed, entry) == "match"]
    if matched:
        return ("one" if len(matched) == 1 else "several"), tuple(matched)
    close = [entry for entry in kept if tier(typed, entry) == "close"][:MAX_CHOICES]
    return ("close", tuple(close)) if close else ("none", ())


def first_step(vocabulary: Vocabulary, customer_id: str, question: str, plan: Plan, narrowed: bool) -> Step:
    """Turn 1 for ``plan``; ``narrowed``: the scope narrows the question on its own."""
    entities = [Entity("person", p.as_typed) for p in plan.people] + [Entity("place", p) for p in plan.places]
    between = _BETWEEN.search(question)
    ends = {end.casefold() for end in between.groups()} if between else set()
    if between and len(ends & {place.casefold() for place in plan.places}) == 2:  # decision 11
        found = vocabulary.near(customer_id, [latin_key(end) for end in ends])
        found = found or vocabulary.candidates(customer_id, [Entity("place", "")])[0]
        phrase, rest = between[0], [entity for entity in entities if entity.as_typed.casefold() not in ends]
        asked = f'"{phrase[0].upper()}{phrase[1:]}" could cover a few places in your documents.'
        asked += " Which should I look in?"
        if found := [entry for entry in found if usable(entry)][:MAX_CHOICES]:
            return _asking(question, (), asked, found, Entity("place", phrase), rest, 1)
    missing = [] if entities or plan.period or narrowed else [k for k in MISSING if k in plan.missing]
    if not missing:
        return next_step(vocabulary, customer_id, question, entities, (), 1)
    newest = vocabulary.candidates(customer_id, [Entity(kind, "") for kind in missing])
    for kind, entries in zip(missing, newest, strict=True):
        if found := [entry for entry in entries if usable(entry)][:MAX_CHOICES]:
            return _asking(question, (), MISSING[kind], found, Entity(kind, ""), (), 1)
    return Step(question, ())


def after_reply(vocabulary: Vocabulary | None, customer_id: str, pending: Pending) -> Step:
    """The picks checked again (one gone since is dropped) and put in the question; then the rest."""
    labels, rest = list(pending.picked), list(pending.remaining)
    if vocabulary is not None and labels:
        found = vocabulary.candidates(customer_id, [Entity(pending.kind, label) for label in labels])
        labels = [label for label, entries in zip(labels, found, strict=True) if _live(label, entries)]
    elif pending.typed:  # B2: typed instead of picked, it is checked as a name of its own
        labels, rest = [pending.typed], [Entity(pending.kind, pending.typed), *rest]
    prefix = "in " if (pending.as_typed or "").casefold().startswith("between") else ""  # decision 11
    question = (
        put(pending.question, pending.as_typed, prefix + _joined(labels)) if labels else pending.question
    )
    if vocabulary is None:
        return Step(question, pending.assumptions)
    return next_step(vocabulary, customer_id, question, rest, pending.assumptions, pending.turn + 1)


def next_step(
    vocabulary: Vocabulary, customer_id: str, question: str, entities: Sequence[Entity], said: Said, turn: int
) -> Step:
    """Each name in turn: a resolved one is put in the question (and ``said`` when its spelling
    changed); the first unclear one is asked while ``turn`` is at most MAX_TURNS, else searched as
    typed, with an open-list line for several matches. One statement."""
    found = vocabulary.candidates(customer_id, entities) if entities else []
    for index, (entity, entries) in enumerate(zip(entities, found, strict=True)):
        result, chosen = outcome(entity, entries)
        typed, label = clean_text(entity.as_typed), clean_text(chosen[0].label) if chosen else ""
        if result == "one" and latin_key(typed, entity.kind) != chosen[0].latin:
            question, said = put(question, entity.as_typed, label), (*said, f"I took {typed} as {label}.")
        elif result in ("several", "close") and turn <= MAX_TURNS:
            asked = f"Did you mean {label}?" if len(chosen) == 1 else "Did you mean one of these?"
            asked = f"Which {typed} do you mean?" if result == "several" else asked
            rest, confirm = entities[index + 1 :], result == "close" and len(chosen) == 1
            return _asking(question, said, asked, chosen, entity, rest, turn, confirm)
        elif result == "several":
            said = (*said, f"I looked for every {typed} in your documents: {_listed(chosen)}.")
    return Step(question, said)


def _asking(
    question: str,
    said: Said,
    asked: str,
    entries: Sequence[Entry],
    about: Entity,
    rest: Any,
    turn: int,
    confirm: bool = False,
) -> Step:
    """The clarify to store and show (decision 13; places several at once, B2), and its resume
    state; ``confirm``: a "Did you mean" whose pick confirms a spelling."""
    choices: list[dict[str, Any]] = []
    for entry in entries:
        if len(choices) == MAX_CHOICES:
            break
        if (label := clean_text(entry.label)) not in (choice["label"] for choice in choices):
            pick = {"id": f"c{len(choices) + 1}", "label": label, "kind": "pick"}
            choices.append({**pick, "document_ids": list(entry.documents)})
    multi = about.kind == "place" and len(choices) > 1
    choices += [ALL, OTHER] if multi else [OTHER]
    clarify = {"id": str(uuid.uuid4()), "question": asked, "choices": choices, "multi": multi}
    resume: dict[str, Any] = {"question": question, "kind": about.kind, "as_typed": about.as_typed or None}
    resume |= {
        "remaining": [[e.kind, e.as_typed] for e in rest],
        "assumptions": list(said),
        "confirm": confirm,
    }
    _log.info("clarify asked", extra={"code": f"clarify_{about.kind}"})
    return Step(question, said, {**clarify, "turn": turn}, resume)


def reply_to(
    answer: Mapping[str, Any], choice_ids: Sequence[str], typed: str | None = None
) -> Pending | None:
    """The stored clarify ``answer`` answered by ``choice_ids`` ("all": every pick) or by the
    ``typed`` words (its resume state as ``_asking`` stored it); None when it is no clarify, or
    an id is not one of its picks (or several are, for a single choice)."""
    clarify, resume = answer.get("clarify") or {}, answer.get("resume") or {}
    picks = {c["id"]: c["label"] for c in clarify.get("choices", []) if c.get("kind") == "pick"}
    every = "all" in choice_ids and clarify.get("multi")
    chosen = list(picks) if every else list(dict.fromkeys(choice_ids))
    if answer.get("status") != "clarify" or not (typed or chosen) or not set(chosen) <= set(picks):
        return None
    if len(chosen) > 1 and not clarify["multi"]:
        return None
    rest = tuple(Entity(kind, name) for kind, name in resume["remaining"])
    fields = {**resume, "remaining": rest, "assumptions": tuple(resume["assumptions"]), "typed": typed}
    return Pending(**fields, turn=clarify["turn"], picked=() if typed else tuple(picks[i] for i in chosen))


def answers_clarify(typed: str) -> bool:
    """Typed words answer the open clarify (B2) only as a short name: at most MAX_ENTITY_CHARS and
    no question mark; anything longer, or a question, is a new question that leaves it."""
    return 0 < len(typed) <= MAX_ENTITY_CHARS and not typed.rstrip().endswith("?")


def put(question: str, as_typed: str | None, said: str) -> str:
    """``question`` with ``as_typed`` (as a whole word, ignoring case) made ``said``, else ``said``
    added at its end: "Ram" is never the start of "Ramnagar", nor राम of रामा."""
    word = f"(?<!{_LETTER}){re.escape(as_typed)}(?!{_LETTER})" if as_typed else None
    found = re.search(word, question, re.IGNORECASE) if word else None
    if found is None:
        return f"{question.rstrip(' ?.')} {said}"
    return question[: found.start()] + said + question[found.end() :]


def _live(label: str, entries: Sequence[Entry]) -> bool:
    return any(entry.latin == latin_key(label, entry.kind) and usable(entry) for entry in entries)


def _listed(entries: Sequence[Entry]) -> str:
    """The first MAX_CHOICES labels, and how many more ("and others" when the statement's
    MAX_CANDIDATES may have left some out), so an open list never claims to be whole."""
    labels = [clean_text(entry.label) for entry in entries[:MAX_CHOICES]]
    more = len(entries) - len(labels)
    if not more:
        return _joined(labels)
    return f"{', '.join(labels)} and {'others' if len(entries) >= MAX_CANDIDATES else f'{more} more'}"


def _joined(labels: Sequence[str]) -> str:
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


def without_resume(answer: Mapping[str, Any]) -> dict[str, Any]:
    """A stored answer as the API shows it: the resume state stays on the server."""
    return {key: value for key, value in answer.items() if key != "resume"}
