"""Following the conversation across scope switches, and related documents (PRD 6 package K, #644).

**Where and what.** The scope chip alone decides where an answer looks; this module never narrows
or widens it. The conversation decides what "this" is: its **focus**, the first document the last
answer cited, else the one document the last question was asked in, while the asker can still see
it (the router's ``last_cited``). A question that points back at it (``points_back``: "this",
"its", याचे, हा, इसका, iska; "it" and "same" only when nothing else is named; never "this month",
या महिन्यात, इस महीने) and names no property number or person of its own (``names_subject``), and that
the rewrite left pointing back, gets the focus's **subject** put in ("Survey 61, Wagholi": its
property and village as stored, else its people, else its name; nothing when the name reads like
instructions or is long), and the answer starts with one line naming the focus and where it looked
(``understood``). "Other", "another", इतर, दूसरे, dusre before a document word, or "elsewhere"
(``asks_other``), leave the focus itself out.

**Related documents** ("related", "similar", संबंधित, जुड़े next to a document word: ``asks_related``)
are answered from the focus's stored land values, never its wording: the documents in scope sharing
a key of it (``value_keys``, #245; its places only when it has no property or person key), ranked by
the same property (its number, and its village on both sides, the same), then the same people, then
the same village or city, a document of the focus's type first on a tie; at most MAX_RELATED, each
with why, from its own values as printed and cited. The focus and its other versions are never
listed. Similar content by meaning is not used yet (PRD 6 lists it; no document-level vector exists).

**Any length.** ``cut`` keeps a question to MAX_ASKED characters: its sentences that end in a
question mark, in order, while they fit, after as much of the rest of its start as the room left
holds; with none, its start and its end. ``parts`` splits a question of several asks at its question
marks, and at a danda after a question word, into at most MAX_PARTS parts, each with a subject (the
service answers each, then all together); a line end is a space, a danda otherwise a full stop.

**Visibility.** Both statements run inside ``db.customer`` (row level security) with ``customer_id
=`` and ``LIVE_CURRENT``; the related one also with the chat's scope. PRD 7 PR 8 (#610): these two
statements are where per-person views replace ``documents`` once it merges.

Time: O(n) in the question's characters; two statements, each over value_keys' B-tree index
(customer_id, kind, latin), at most MAX_KEYS keys and MAX_ROWS rows; O(r log r) to rank r rows.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, TextClause, text
from sqlalchemy.exc import OperationalError

from kaagaz.answering import glossary
from kaagaz.answering.clarify import PROPERTY_KINDS, Entry, latin_key, property_choice
from kaagaz.answering.evidence import looks_like_instructions, valid_regions
from kaagaz.answering.fixed_queries import ValueRef
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.sanitize import clean_text
from kaagaz.chunking.script import has_other_script_letters, spaced_words
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import identifiers
from kaagaz.search.scope import Scope

_log = logging.getLogger(__name__)

MAX_PARTS, MAX_ASKED, MAX_RELATED = 4, 1000, 10
MAX_KEYS, MAX_ROWS = 40, 400  # the focus's keys looked up, and the rows of other documents read
STATEMENT_TIMEOUT_MS = 3000  # as a search statement's (search/hybrid.py)
_QUERY_CANCELED = "57014"
_WEIGHTS = {"property": 8.0, "person": 4.0, "place": 1.0}  # PRD 6: strongest, strong, medium
_SAID = {"property": "same", "person": "same person", "place": "same place"}

_LETTER = r"[\wऀ-ॿ]"  # a letter, digit or Devanagari sign: a word goes on
# Bare Hindi या is "or", never "this"; याचा, यात and the rest are Marathi's "this".
_POINTS = re.compile(
    f"(?<!{_LETTER})(?:this|its|these|those"
    r"|that\s+(?:one|document|deed|land|record|property|plot|file|extract|gat|survey)"
    "|याचा|याची|याचे|याच्या|याच्याशी|यात|याला|यातील|ह्या|हा|हे"
    "|यह|इस|इसका|इसकी|इसके|इसमें|इससे|इसी"
    f"|iska|iski|iske|isme|isse|yeh)(?!{_LETTER})",
    re.IGNORECASE,
)
# "it" (never "IT", a return) and "same" (never "same day") point back only when nothing else is named.
_WEAK = re.compile(f"(?<!{_LETTER})(?:it|It|same(?!\\s+(?:day|date|month|year|time|week)\\b))(?!{_LETTER})")
_DATED = re.compile(
    r"\bthis\s+(?:month|year|week|quarter|fy|financial\s+year)\b"
    r"|(?:या|ह्या|हा|हे)\s+(?:महिन\w*|वर्ष\w*|आठवड\w*)"
    r"|इस\s+(?:महीने|महीना|साल|वर्ष|हफ्ते|हफ़्ते|सप्ताह)"
    r"|\bis\s+(?:mahine|mahina|saal|hafte|month|year)\b",
    re.IGNORECASE,
)
_PERSON = re.compile(r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b")  # "Ramesh Patil": a name typed as one
_OTHER = frozenset(
    {"other", "others", "another", "इतर", "दुसऱ्या", "दुसरे", "दुसरी", "दुसरा", "अन्य", "दूसरे", "दूसरी"}
    | {"दूसरा", "बाकी", "dusre", "dusri", "dusra", "doosre", "baaki", "itar"}
)
_RELATED = frozenset(
    {"related", "relating", "similar", "संबंधित", "सम्बंधित", "संबंधी", "जुड़े", "जुडे", "जुड़ी", "जुड़ा"}
    | {"मिलते", "sambandhit", "jude", "judi", "milte"}
)
_DOCUMENT_NOUNS = frozenset(
    {"document", "documents", "doc", "docs", "file", "files", "record", "records", "paper", "papers"}
    | {"deed", "deeds", "extract", "extracts", "dastavez", "dastavej", "kagaz", "kagad", "kagadpatre"}
)
_DOCUMENT_STEMS = ("कागदपत्र", "कागद", "दस्तऐवज", "दस्तावेज", "कागज", "फाइल", "फ़ाइल", "रेकॉर्ड")
_QUESTION_WORDS = frozenset(
    {"काय", "कोण", "कोणी", "कोणाचा", "कोणाचे", "किती", "कुठे", "केव्हा", "कधी", "कसे", "कोणत्या", "कोणता"}
    | {"कोणते", "क्या", "कौन", "कितना", "कितनी", "कितने", "कहाँ", "कहां", "कब", "क्यों", "कैसे", "किस"}
    | {"किसका", "किसकी", "किसके", "kya", "kaun", "kitna", "kitni", "kitne", "kahan", "kab", "kyun", "kaise"}
)
_SENTENCE_END = re.compile(r"(?<=[?!।॥]) |(?<=\.) (?=[A-Zऀ-ॿ])")  # over text whose spaces are single
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,4}$")  # rewrite.py's: never "24.02.2003"
MAX_NAME_CHARS = 80  # a focus's name, as the understood line and a question show it


def names_subject(question: str) -> bool:
    """The question names a property number with its kind ("Gat 12", "गट ६१") or a person typed as
    one ("Ramesh Patil"), so "its" and "this" are about that, not the focus. O(n)."""
    return bool(identifiers(question)) or bool(_PERSON.search(question))


def points_back(question: str) -> bool:
    """The question points back at something already said ("this", "its", याचे, iska; "it" and
    "same" when nothing else is named), never a period ("this month", या महिन्यात). O(n)."""
    plain = _DATED.sub(" ", question)
    return bool(_POINTS.search(plain)) or (bool(_WEAK.search(plain)) and not names_subject(plain))


def _word_list(question: str) -> list[str]:
    return [word.casefold() for word in spaced_words(question)]


def _document_word(word: str) -> bool:
    return word in _DOCUMENT_NOUNS or word.startswith(_DOCUMENT_STEMS)


def asks_other(question: str) -> bool:
    """ "Any other document", "another deed", इतर कागदपत्रात, दूसरे दस्तावेज़, "anywhere else",
    "elsewhere": the focus itself is left out. Never "the other party" or "what else". O(n)."""
    words = _word_list(question)
    for at, word in enumerate(words):
        if word == "elsewhere" or (word == "else" and at and words[at - 1] in {"anywhere", "somewhere"}):
            return True
        if word in _OTHER and any(map(_document_word, words[at + 1 : at + 3])):
            return True
    return False


def asks_related(question: str) -> bool:
    """Related or similar documents are asked for (PRD 6 package K): a related word next to a
    document word ("related documents", "documents similar to", संबंधित कागदपत्रे). O(n)."""
    words = _word_list(question)
    return any(
        word in _RELATED and any(map(_document_word, [*words[max(0, at - 2) : at], *words[at + 1 : at + 3]]))
        for at, word in enumerate(words)
    )


# Words a request for the focus's related documents may hold besides its related word, so "show
# related documents" is about the focus, and "documents related to Nikhil Varde" is not.
_RELATED_FRAME = _RELATED | _OTHER | _DOCUMENT_NOUNS | {"land", "property", "else", "elsewhere"}


def about_focus(question: str) -> bool:
    """The question is about the focus: it points back, or names nothing but related documents."""
    named = set(glossary.content_words(normalise(question).keywords))
    return points_back(question) or named <= _RELATED_FRAME


def _has_subject(part: str) -> bool:
    """A content word, or letters of another script, so a part asks about something."""
    return bool(glossary.content_words(normalise(part).keywords)) or has_other_script_letters(part)


def parts(question: str) -> tuple[list[str], int]:
    """The question's parts with a subject, at most MAX_PARTS, and how many there are in all: it
    is split after each "?", and after a danda that ends a clause holding a question word ("इसका
    मालिक कौन है।"); a line end is a space. One part when it asks one thing. O(n * d) for d dandas."""
    text, found, start = " ".join(question.split()), [], 0
    for at, mark in enumerate(text):
        asks = mark == "?" or (mark in "।॥" and bool(_QUESTION_WORDS & set(_word_list(text[start:at]))))
        if asks:
            found.append(text[start : at + 1].strip())
            start = at + 1
    found.append(text[start:].strip())
    asked = [part for part in found if part and _has_subject(part)]
    return asked[:MAX_PARTS], len(asked)


def cut(question: str, most: int = MAX_ASKED) -> str:
    """``question`` when it is at most ``most`` characters; else its sentences that end in a "?", in
    order, while they fit, after as much of its other sentences, from its start, as the room left
    holds (cut at a word, then " … "); with none, its start and its end around " … ". O(n)."""
    if len(question) <= most:
        return question
    text = " ".join(question.split())  # one space between words: no regex runs over a long gap
    sentences = _SENTENCE_END.split(text)
    kept: list[str] = []
    for sentence in sentences:
        if sentence.endswith("?") and len(" ".join([*kept, sentence])) <= most:
            kept.append(sentence)
    if kept:
        asked = " ".join(kept)
        rest = " ".join(s for s in sentences if not s.endswith("?"))
        room = most - len(asked) - len(" … ")
        head = rest[:room].rsplit(" ", 1)[0].rstrip() if len(rest) > room else rest
        return f"{head} … {asked}" if head and room > 0 else asked
    half = (most - 3) // 2
    head, tail = text[:half].rsplit(" ", 1)[0], text[-half:].split(" ", 1)[-1]
    return f"{head.rstrip()} … {tail.lstrip()}"


@dataclass(frozen=True, slots=True)
class Fact:
    """One keyed land value: its ``value_keys`` kind and latin, its record's village as stored (a
    property's; else ""), the value with its spot, and its document's type."""

    kind: str
    latin: str
    village: str
    value: ValueRef
    doc_type: str


@dataclass(frozen=True, slots=True)
class Profile:
    """The focus: its id, name and type as stored, and its keyed values (none for an invoice)."""

    document_id: str
    name: str
    doc_type: str
    facts: tuple[Fact, ...]


@dataclass(frozen=True, slots=True)
class Related:
    document_id: str
    name: str
    why: str
    refs: tuple[ValueRef, ...]
    score: float


def _named(name: str) -> str:
    """The name as a question or the understood line shows it; "" when it reads like instructions
    or is over MAX_NAME_CHARS (a document's name is the customer's text, untrusted)."""
    shown = _EXTENSION.sub("", clean_text(name)).strip()
    return "" if len(shown) > MAX_NAME_CHARS or looks_like_instructions(shown) else shown


def _label(item: Fact) -> str | None:
    """A property's label ("Survey 61, Wagholi": clarify's, without the type); a person's or a
    place's text; None when it is no choice clarify would offer or reads like instructions."""
    printed = clean_text(item.value.value_text)
    if item.kind != "property":
        return None if not printed or looks_like_instructions(printed) else printed
    entry = Entry(
        "property",
        item.latin,
        item.value.value_text,
        (item.value.document_id,),
        field=item.value.field,
        village=item.village,
        doc_name=item.value.document_name,
    )
    built = property_choice(entry)
    return None if built is None else built[0].split(" · ")[0]


def subject(profile: Profile) -> str:
    """What "this" is: the focus's properties (at most two), else its people (two), else its name."""
    for kind in ("property", "person"):
        labels = [label for item in profile.facts if item.kind == kind if (label := _label(item))]
        if labels:
            return "; ".join(list(dict.fromkeys(labels))[:2])
    return _named(profile.name)


def understood(profile: Profile, *, narrowed: bool, other: bool) -> str | None:
    """The answer's first line (PRD 6): what "this" was taken as, and where it looked; None with
    no subject to say."""
    where = {
        (False, False): "all your documents",
        (False, True): "your other documents",
        (True, False): "the documents you chose",
        (True, True): "the other documents you chose",
    }[(narrowed, other)]
    said, name = subject(profile), _named(profile.name)
    if not said:
        return None
    return (
        f"Searching {where} for {said} (from {name})."
        if name and said != name
        else f"Searching {where} for {said}."
    )


def related(profile: Profile, found: Sequence[Fact]) -> list[Related]:
    """The documents of ``found`` sharing a key with ``profile``, ranked (the module docstring), at
    most MAX_RELATED. A value with no spot, or that reads like instructions, is no reason; the same
    number in another village is another land. O(r log r)."""
    mine: dict[tuple[str, str], set[str]] = {}
    for item in _keyed(profile):
        mine.setdefault((item.kind, item.latin), set()).add(latin_key(item.village))
    reasons: dict[str, dict[tuple[str, str], tuple[str, ValueRef]]] = {}
    kinds: dict[str, str] = {}
    for item in found:
        document = item.value.document_id
        villages = mine.get((item.kind, item.latin))
        if document == profile.document_id or villages is None or not valid_regions([item.value.region]):
            continue
        if item.kind == "property" and not ({latin_key(item.village)} & villages - {""}):
            continue  # the same number with no village on a side, or in another village
        label = _label(item)
        if label is not None:
            reasons.setdefault(document, {}).setdefault((item.kind, item.latin), (label, item.value))
            kinds.setdefault(document, item.doc_type)
    listed = []
    for document, held in reasons.items():
        score = sum(_WEIGHTS[kind] for kind, _ in held) + 0.5 * (kinds[document] == profile.doc_type)
        why = "; ".join(f"{_SAID[kind]} {label}" for (kind, _), (label, _) in held.items())
        why = why[0].upper() + why[1:]
        name = next(iter(held.values()))[1].document_name
        refs = tuple(value for _, value in held.values())
        listed.append(Related(document, name, why, refs, score))
    listed.sort(key=lambda r: (-r.score, r.name.casefold(), r.document_id))
    return listed[:MAX_RELATED]


def _keyed(profile: Profile) -> list[Fact]:
    """The focus's facts related documents are looked up by: its places only when it has no
    property or person (a village is shared by too many documents to say much)."""
    named = [item for item in profile.facts if item.kind != "place"]
    return named or list(profile.facts)


# LIVE_CURRENT (kaagaz/db/visibility.py), written out; a property's own fields only (a boundary's
# number is a neighbour's); its record's village. PRD 7 PR 8 (#610): per-person views here.
_VALUE_COLUMNS = (
    "k.kind, k.latin, v.id::text AS value_id, v.document_id::text AS document_id, d.display_name,"
    " d.detected_type, v.page_no, v.region, v.field, v.value_text, v.label, v.label_page_no,"
    " v.label_region, v.label_text, d.doc_type, pl.village"
)
_KEYED = (
    " JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id"
    " LEFT JOIN LATERAL (SELECT max(p.value_text) AS village FROM extracted_values p"
    " WHERE k.kind = 'property' AND p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.record_key = v.record_key AND p.field = 'land_village') pl ON true"
)
_KINDS = (
    " AND (k.kind <> 'property' OR v.field = ANY(CAST(:property_fields AS text[])))"
    " AND (k.kind <> 'place' OR k.place_level IN ('village', 'city'))"
)
_ORDER = " ORDER BY CASE k.kind WHEN 'property' THEN 0 WHEN 'person' THEN 1 ELSE 2 END"
_PROFILE = text(
    "SELECT d.display_name AS name, d.doc_type AS focus_type, x.* FROM documents d"  # noqa: S608 - constants
    f" LEFT JOIN LATERAL (SELECT {_VALUE_COLUMNS} FROM value_keys k{_KEYED}"
    " WHERE k.customer_id = d.customer_id AND k.document_id = d.id"
    f"{_KINDS}{_ORDER}, v.page_no, v.id LIMIT :most) x ON true"
    " WHERE d.customer_id = :customer_id AND d.id = CAST(:focus AS uuid)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
)
# One row a document and key (its first value), so a key printed many times reads one row.
_MATCHES = text(
    f"SELECT * FROM (SELECT DISTINCT ON (k.document_id, k.kind, k.latin) {_VALUE_COLUMNS}"  # noqa: S608 - constants
    " FROM unnest(CAST(:kinds AS text[]), CAST(:latins AS text[])) AS m(kind, latin)"
    " JOIN value_keys k ON k.customer_id = :customer_id AND k.kind = m.kind AND k.latin = m.latin"
    f"{_KEYED} JOIN documents d ON d.customer_id = k.customer_id AND d.id = k.document_id"
    " WHERE k.document_id <> CAST(:focus AS uuid)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))"
    " GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
    f"{_KINDS} ORDER BY k.document_id, k.kind, k.latin, v.page_no, v.id) x"
    " ORDER BY CASE x.kind WHEN 'property' THEN 0 WHEN 'person' THEN 1 ELSE 2 END,"
    " lower(x.display_name), x.value_id LIMIT :most"
)
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")


def _fact(row: Row[Any]) -> Fact:
    m = row._mapping
    ref = ValueRef(
        m["value_id"],
        m["document_id"],
        m["display_name"],
        m["detected_type"],
        m["page_no"],
        m["region"],
        m["field"],
        m["value_text"],
        m["label"],
        m["label_page_no"],
        m["label_region"],
        m["label_text"],
    )
    return Fact(m["kind"], m["latin"], m["village"] or "", ref, m["doc_type"] or "")


class PgFocus:
    """The focus's profile and its related values, one statement each in one ``db.customer``
    transaction with its own statement timeout. A statement that times out (57014) gives no focus,
    or no related value, with code ``focus_timeout`` logged: the answer goes on without it."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def profile(self, customer_id: str, document_id: str) -> Profile | None:
        """``document_id``'s profile; None when the asker's organisation cannot see it now."""
        params = {"customer_id": customer_id, "focus": document_id, "most": MAX_KEYS}
        rows = self._rows(customer_id, _PROFILE, {**params, "property_fields": sorted(PROPERTY_KINDS)})
        if not rows:
            return None
        first = rows[0]._mapping
        facts = tuple(_fact(row) for row in rows if row._mapping["value_id"] is not None)
        return Profile(document_id, str(first["name"]), str(first["focus_type"] or ""), facts)

    def matches(self, customer_id: str, profile: Profile, scope: Scope) -> list[Fact]:
        """Other documents' keyed values in ``scope`` sharing a key of ``profile``."""
        keys = list(dict.fromkeys((item.kind, item.latin) for item in _keyed(profile)))
        if not keys:
            return []
        params = {
            "customer_id": customer_id,
            "focus": profile.document_id,
            "kinds": [kind for kind, _ in keys],
            "latins": [latin for _, latin in keys],
            "property_fields": sorted(PROPERTY_KINDS),
            "most": MAX_ROWS,
            **scope.params(),
        }
        return [_fact(row) for row in self._rows(customer_id, _MATCHES, params)]

    def _rows(self, customer_id: str, statement: TextClause, params: dict[str, Any]) -> list[Row[Any]]:
        try:
            with self._db.customer(customer_id) as conn:
                conn.execute(_SET_TIMEOUT, {"ms": str(STATEMENT_TIMEOUT_MS)})
                return list(conn.execute(statement, params))
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) != _QUERY_CANCELED:
                raise
            _log.warning("focus statement dropped", extra={"code": "focus_timeout"})
            return []
