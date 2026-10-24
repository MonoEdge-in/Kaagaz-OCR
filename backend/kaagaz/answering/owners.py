"""Owner and place questions (PRD 6 package E, 24 Oct 2026): "land owned by Nikhil Varde and
Krishna Sohoni", "the property owned in Pune", "the unowned percentage of survey 45/2".

Answered by fixed queries from the land values (#245: ``land_*`` fields in ``extracted_values``,
a property's record ``land:{p}``, its parties ``land:{p}:party:{o}``) and the company vocabulary
``value_keys``; code writes every word. Owning (decision 12) is an owner on a 7/12 extract or a
buyer on a sale deed, never a seller.

**The question** (``owner_ask``; B's plan gives the same ``OwnerAsk``): runs of one to four of
its words in their Latin spelling (a word with a digit also as an identifier, 45/2) are looked
up exactly in ``value_keys``. It routes when it names people, places or properties the
company's documents hold, with an owning word (own, owned, bought...) or a share word.

**The queries**, each inside ``db.customer``, ``WHERE customer_id =``, the ``LIVE_CURRENT``
text and the chat's scope, with only ``=`` and ``= ANY`` on B-tree indexes (trigram and array
operators are not used under row level security, 0011): ``_RESOLVE``; ``_OWNED`` (owned by,
owned together: grouped by record, ``count(DISTINCT latin)`` against how many were named, the
relational division of the tags scope; records naming only some are offered, never mixed in);
``_KEYED`` (in places, at any level; a survey or khata number the same way); and
``_SHARES_AS_PRINTED``, every land value of the records found with its spot: a share is quoted
as printed, never added, subtracted or derived, the unowned share only where printed.

**Map questions** ("show the map of survey 45/2", "the length of plot 12", "which survey numbers
border 45/2"): a map, measurement or border word and a property, with no owning or share word.
``_KEYED`` finds the records of that survey, khata or plot number (and, for borders, those
printing it as a neighbour, a ``land_boundary_*`` key); ``_SHARES_AS_PRINTED`` gives their map
values. Each is quoted as printed with its unit and cited; one asked for and not printed is
said to be not printed, never worked out from the others, the drawing or the scale.

Time: at most four key statements and one for the values, O(k) index probes for k keys and
O(v) in the values of the records found.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from kaagaz.answering.fixed_queries import ValueRef
from kaagaz.answering.hinglish import STOP_WORDS
from kaagaz.answering.sanitize import clean_text
from kaagaz.chunking.script import fold_digits, latin_spelling, spaced_words
from kaagaz.db import ai_settings
from kaagaz.db.engine import Database
from kaagaz.extraction.land import SIDES
from kaagaz.extraction.links import normalise_identifier
from kaagaz.search.scope import Scope

MAX_LISTED = 50  # land records listed in one answer
MAX_NAMED = 3  # owners a record's line names when none was asked for, or more own it
MAX_GROUPS = 6  # owners and groups of owners an open list names
_MAX_RUN = 4  # words in a name or place
_KINDS = ("person", "place", "property")

OWNING = ("land_owner_name", "land_buyer_name")  # decision 12: never land_seller_name
_ROLES = {"land_owner_name": "owners", "land_buyer_name": "buyers", "land_seller_name": "sellers"}
_PLACES = ("land_village", "land_taluka", "land_district", "land_city")  # in this order on a line
_PLOT = "land_plot_number"
_PROPERTIES = ("land_survey_number", "land_khata_number", _PLOT)
_SHARE, _UNOWNED, _SURVEY = "land_share", "land_unowned_share", "land_survey_number"
# A map's values as printed (extraction/land.py), in this order on a line, each with its words.
_MEASURES = {
    "land_length": "length",
    "land_width": "width",
    "land_area": "area",
    "land_road_width": "road width",
    "land_dimension": "other measurements",
    "land_scale": "scale",
}
_SIDES = {f"land_boundary_{side}": side for side in SIDES}
_FIELDS = (*_ROLES, _SHARE, _UNOWNED, _SURVEY, _PLOT, *_PLACES, *_MEASURES, *_SIDES)

_OWN = frozenset({"own", "owns", "owned", "owner", "owners", "ownership", "belong", "belongs", "bought"})
_OWN |= {"malik", "maalik", "malak"}
_SHARES = frozenset({"share", "shares", "percentage", "percent", "hissa", "unowned", "unallotted"})
_UNOWNED_WORDS = frozenset({"unowned", "unallotted", "rest", "remaining"})
_MEASURE_WORDS = {
    **dict.fromkeys(("length", "long"), "land_length"),
    **dict.fromkeys(("width", "wide", "breadth"), "land_width"),
    "area": "land_area",
    **dict.fromkeys(("road", "frontage"), "land_road_width"),
    "scale": "land_scale",
}
_BORDER_WORDS = frozenset(
    {"border", "borders", "bordering", "boundary", "boundaries", "adjoining", "adjacent"}
)
_BORDER_WORDS |= {"neighbour", "neighbours", "neighbor", "neighbors"}
_MAP_WORDS = frozenset({"map", "maps", "naksha", "sketch", "layout", "dimension", "dimensions", "size"})
_MAP_WORDS |= {"measurement", "measurements", "sides"}
_FRAME = _OWN | _SHARES | _UNOWNED_WORDS | STOP_WORDS | {"land", "property", "properties", "survey", "plot"}
_FRAME |= {*_MEASURE_WORDS, *_BORDER_WORDS, *_MAP_WORDS}
_LAND_WORDS = frozenset(
    {"survey", "plot", "gat", "khata", "land", "property", "map", "maps", "naksha", "sketch"}
)


@dataclass(frozen=True, slots=True)
class OwnerAsk:
    """What an owner or place question asks, as ``value_keys.latin`` keys."""

    people: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    properties: tuple[str, ...] = ()
    shares: bool = False  # the shares as printed
    unowned: bool = False  # the unowned share
    measures: tuple[str, ...] = ()  # the map values asked for by name (``_MEASURES``' fields)
    borders: bool = False  # the neighbours by side
    drawn: bool = False  # the map itself: every value printed on it


def owner_ask(question: str, resolve: Callable[[list[str]], list[tuple[str, str]]]) -> OwnerAsk | None:
    """The ask, or None. ``resolve(candidates)``, the (kind, latin) of those the company's documents
    name, is called only for a question with an owning, share or map word; a map question asks
    about properties only. O(n) for n words."""
    words = [word.casefold() for word in spaced_words(fold_digits(question))]
    owning, shares = bool(_OWN.intersection(words)), bool(_SHARES.intersection(words))
    measures = tuple(
        dict.fromkeys(
            _MEASURE_WORDS[word]
            for before, word in zip(["", *words], words, strict=False)
            if word in _MEASURE_WORDS and not (word == "width" and before in ("road", "frontage"))
        )
    )
    borders, drawn = bool(_BORDER_WORDS.intersection(words)), bool(_MAP_WORDS.intersection(words))
    # A map question names the land ("survey 45/2", "plot 12", "the map"): "the size of invoice 12"
    # is never one, even where a survey 12 is keyed.
    mapped = (bool(measures) or borders or drawn) and bool(_LAND_WORDS.intersection(words))
    if not owning and not shares and not mapped:
        return None
    at: dict[str, int] = {}  # each candidate, and where it first starts in the question
    for start in range(len(words)):
        for run in (words[start : start + size] for size in range(1, _MAX_RUN + 1)):
            if not all(word in _FRAME for word in run):
                at.setdefault(" ".join(latin_spelling(" ".join(run)).split()), start)
        if any(ch.isdigit() for ch in words[start]):
            at.setdefault(normalise_identifier(words[start]), start)
    found = set(resolve(list(at)))
    people, places, properties = (
        tuple(sorted((key for k, key in found if k == kind), key=at.__getitem__)) for kind in _KINDS
    )
    unowned = bool(_UNOWNED_WORDS.intersection(words))
    if not owning and not shares:
        return (
            OwnerAsk(properties=properties, measures=measures, borders=borders, drawn=drawn)
            if properties
            else None
        )
    return OwnerAsk(people, places, properties, shares, unowned) if people or places or properties else None


_RESOLVE = text(
    """
    SELECT DISTINCT k.kind, k.latin
    FROM value_keys k
    JOIN documents d ON d.customer_id = k.customer_id AND d.id = k.document_id
    WHERE k.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND k.kind = ANY(ARRAY['person', 'place', 'property']) AND k.latin = ANY(CAST(:candidates AS text[]))
    """
)

_OWNED = text(
    """
    SELECT v.document_id::text AS document_id, split_part(v.record_key, ':party:', 1) AS land,
           count(DISTINCT k.latin) = cardinality(CAST(:people AS text[])) AS every
    FROM value_keys k
    JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    WHERE k.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND k.kind = 'person' AND k.latin = ANY(CAST(:people AS text[]))
      AND v.field = ANY(CAST(:fields AS text[]))
    GROUP BY v.document_id, split_part(v.record_key, ':party:', 1), d.display_name
    ORDER BY every DESC, lower(d.display_name), document_id, land
    """
)

_KEYED = text(
    """
    SELECT v.document_id::text AS document_id, v.record_key AS land, lower(d.display_name) AS name
    FROM value_keys k
    JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    WHERE k.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND k.kind = :kind AND k.latin = ANY(CAST(:keys AS text[]))
      AND v.field = ANY(CAST(:fields AS text[]))
    GROUP BY v.document_id, v.record_key, d.display_name
    ORDER BY name, document_id, land
    """
)

_SHARES_AS_PRINTED = text(
    """
    SELECT v.record_key, k.latin, v.id::text AS value_id, v.document_id::text AS document_id,
           d.display_name, d.detected_type, v.page_no, v.region, v.field, v.value_text, v.label,
           v.label_page_no, v.label_region
    FROM extracted_values v
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    LEFT JOIN value_keys k ON k.customer_id = v.customer_id AND k.value_id = v.id
    WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND v.document_id = ANY(CAST(:documents AS uuid[])) AND v.field = ANY(CAST(:fields AS text[]))
    ORDER BY v.document_id, length(v.record_key), v.record_key, v.page_no, v.id
    """
)


class PgOwners:
    """The queries, one statement a call, each inside ``db.customer``. ``owned``: (document_id,
    record, every named person owns it) where any owns; ``keyed``: (document_id, record) at the
    places, or of the properties, of ``keys``; ``shares``: (record_key, its key's latin or None,
    value) for every land value of ``documents``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def _rows(self, customer_id: str, query: Any, params: dict[str, Any], scope: Scope) -> list[Any]:
        with self._db.customer(customer_id) as conn:
            return list(conn.execute(query, {"customer_id": customer_id, **params, **scope.params()}))

    def resolve(self, customer_id: str, candidates: Sequence[str], scope: Scope) -> list[tuple[str, str]]:
        """Nothing while the ``land_facts`` switch is off (0052): the other routes answer."""
        with self._db.customer(customer_id) as conn:
            if not ai_settings.switch_on(conn, "land_facts", customer_id):
                return []
            params = {"customer_id": customer_id, "candidates": list(candidates), **scope.params()}
            return [(str(row.kind), str(row.latin)) for row in conn.execute(_RESOLVE, params)]

    def owned(self, customer_id: str, people: Sequence[str], scope: Scope) -> list[tuple[str, str, bool]]:
        rows = self._rows(customer_id, _OWNED, {"people": list(people), "fields": list(OWNING)}, scope)
        return [(row.document_id, row.land, bool(row.every)) for row in rows]

    def keyed(
        self, customer_id: str, kind: str, keys: Sequence[str], scope: Scope, fields: Sequence[str] = ()
    ) -> list[tuple[str, str]]:
        """``fields``: the keyed fields to look in, by default the kind's own (a neighbour's
        number is a property key on a ``land_boundary_*`` field)."""
        fields = list(fields or (_PLACES if kind == "place" else _PROPERTIES))
        rows = self._rows(customer_id, _KEYED, {"kind": kind, "keys": list(keys), "fields": fields}, scope)
        return [(row.document_id, row.land) for row in rows]

    def shares(
        self, customer_id: str, documents: Sequence[str], scope: Scope
    ) -> list[tuple[str, str | None, ValueRef]]:
        params = {"documents": list(documents), "fields": list(_FIELDS)}
        rows = self._rows(customer_id, _SHARES_AS_PRINTED, params, scope)
        return [(str(row.record_key), row.latin, ValueRef(*row[2:])) for row in rows]  # its columns, in order


# --- the answer, in code -----------------------------------------------------


@dataclass(slots=True)
class _Party:
    role: str  # "owners", "buyers" or "sellers"
    name: ValueRef
    person: str  # its value_keys.latin, else its own text casefolded
    share: ValueRef | None = None


@dataclass(slots=True)
class _Land:
    document_id: str
    title: str
    parties: list[_Party] = field(default_factory=list)
    places: list[tuple[ValueRef, str | None]] = field(default_factory=list)  # with its latin key
    survey: ValueRef | None = None
    unowned: ValueRef | None = None
    plot: ValueRef | None = None
    printed: dict[str, list[tuple[ValueRef, str | None]]] = field(default_factory=dict)  # map values, keys

    def owning(self) -> list[_Party]:
        return [party for party in self.parties if party.role != "sellers"]


Cited = list[ValueRef]


@dataclass(frozen=True, slots=True)
class Reply:
    """Not ``answered``: ``closest`` names the records matching only part of the question."""

    answered: bool
    sentences: list[tuple[str, Cited]]
    documents: list[tuple[str, str, str, Cited]]
    closest: list[dict[str, str]]


def reply(source: PgOwners, customer_id: str, ask: OwnerAsk, scope: Scope) -> Reply | None:
    """The answer to ``ask`` from ``source``, or None when no land record matches any of it
    (the other routes then answer). Every number in it is a count of what it lists or cites,
    a figure as printed and cited, or a page."""
    if ask.measures or ask.borders or ask.drawn:
        return _map_reply(source, customer_id, ask, scope)
    owned = source.owned(customer_id, ask.people, scope) if ask.people else []
    keyed = [
        source.keyed(customer_id, kind, keys, scope)
        for kind, keys in (("place", ask.places), ("property", ask.properties))
        if keys
    ]
    within = set.intersection(*(set(records) for records in keyed)) if keyed else None
    rows = owned if ask.people else [(d, land, True) for d, land in (keyed[0] if keyed else [])]
    kept = [(d, land, every) for d, land, every in rows if within is None or (d, land) in within]
    found = [(d, land) for d, land, every in kept if every]
    some = [(d, land) for d, land, every in kept if not every]
    if not found and not some:
        return None
    listed, partly = found[:MAX_LISTED], some[:MAX_LISTED]
    lands = _lands(source.shares(customer_id, sorted({d for d, _ in listed + partly}), scope))
    if not found:
        titles = {d: lands[(d, land)].title for d, land in partly if (d, land) in lands}
        return Reply(False, [], [], [{"id": d, "display_name": title} for d, title in titles.items()])
    records = [lands[record] for record in listed if record in lands]
    others = [lands[record] for record in partly if record in lands]
    if ask.shares:
        sentences = [line for land in records for line in _share_lines(land, ask.unowned)]
    elif ask.people:
        sentences = _owned_lines(ask.people, records, others, len(found))
    else:
        line, records = _open_list(set(ask.places), records)
        sentences = [line]
    for land in records:
        hidden = _hidden(land, ask.people)
        if hidden:
            shown = _and([_name(p) for p in land.owning() if p not in hidden])
            text = f"{_called(land)} names {shown} and {len(hidden)} more owners."
            sentences.append((text, [p.name for p in hidden]))
    return Reply(True, sentences, [_document(land, ask.people) for land in records], [])


def _lands(values: Sequence[tuple[str, str | None, ValueRef]]) -> dict[tuple[str, str], _Land]:
    """The land records of ``values`` (in record and party order), keyed (document_id, record)."""
    lands: dict[tuple[str, str], _Land] = {}
    parties: dict[tuple[str, str], _Party] = {}
    shares: dict[tuple[str, str], ValueRef] = {}
    for key, latin, ref in values:
        record = (ref.document_id, key.split(":party:")[0])
        land = lands.setdefault(record, _Land(ref.document_id, clean_text(ref.document_name)))
        if ref.field in _ROLES and (ref.document_id, key) not in parties:
            person = latin or " ".join(ref.value_text.casefold().split())
            parties[(ref.document_id, key)] = party = _Party(_ROLES[ref.field], ref, person)
            land.parties.append(party)
        elif ref.field == _SHARE:
            shares.setdefault((ref.document_id, key), ref)
        elif ref.field in _PLACES:
            land.places.append((ref, latin))
        elif ref.field == _SURVEY:
            land.survey = land.survey or ref
        elif ref.field == _UNOWNED:
            land.unowned = land.unowned or ref
        elif ref.field == _PLOT:
            land.plot = land.plot or ref
        elif ref.field in _MEASURES or ref.field in _SIDES:
            land.printed.setdefault(ref.field, []).append((ref, latin))
    for spot, party in parties.items():
        party.share = shares.get(spot)
    for land in lands.values():
        land.places.sort(key=lambda place: _PLACES.index(place[0].field))
    return lands


def _owned_lines(
    people: Sequence[str], records: list[_Land], partly: list[_Land], total: int
) -> list[tuple[str, Cited]]:
    """'Here are 3 land records that name both Nikhil Varde and Krishna Sohoni as owners.'
    and '2 more records name only one of them. Want to see those too?'"""
    matched = [p for land in records for p in land.owning() if p.person in people]
    first = {p.person: _name(p) for p in reversed(matched)}  # as each is first printed
    names = _and([first[person] for person in people if person in first])
    role = "an owner" if len(people) == 1 else "owners"
    both = "" if len(people) == 1 else "both " if len(people) == 2 else "all of "
    if total == 1:
        head = f"Here is 1 land record that names {both}{names} as {role}."
    else:
        count = str(total) if len(records) == total else f"the first {len(records)} of {total}"
        head = f"Here are {count} land records that name {both}{names} as {role}."
    lines = [(head, [p.name for p in matched])]
    if partly:
        more = "1 more record names" if len(partly) == 1 else f"{len(partly)} more records name"
        which = "one" if len(people) == 2 else "some"
        cited = [next(p.name for p in land.owning() if p.person in people) for land in partly]
        lines.append((f"{more} only {which} of them. Want to see those too?", cited))
    return lines


def _open_list(places: set[str], records: list[_Land]) -> tuple[tuple[str, Cited], list[_Land]]:
    """'You didn't say whose, so here is everyone in your documents who owns property in Pune
    district: Nikhil Varde owns 2, Krishna Sohoni owns 1, and they share 1.', and the records
    in that order: each owner's alone, most first, then those owned together."""
    spots = [ref for land in records for ref, latin in land.places if latin in places]
    words = list(dict.fromkeys(clean_text(ref.value_text) for ref in spots))
    levels = {ref.field.removeprefix("land_") for ref in spots}
    where = "these records" if not words else "property in " + _and(words, "or")
    if len(words) == 1 and len(levels) == 1:
        where += " " + levels.pop()
    groups: dict[tuple[str, ...], list[_Land]] = {}
    for land in records:
        groups.setdefault(tuple(sorted({p.person for p in land.owning()})), []).append(land)
    order = sorted(groups.items(), key=lambda group: (len(group[0]) != 1, -len(group[1])))
    names = {p.person: _name(p) for land in records for p in land.owning()}
    parts = []
    for people, lands in order[:MAX_GROUPS]:
        if not people:
            parts.append(f"no owner is named on {len(lands)}")
        elif len(people) == 1:
            parts.append(f"{names[people[0]]} owns {len(lands)}")
        else:
            who = "they" if len(people) == 2 == len(names) else _and([names[p] for p in people])
            parts.append(f"{who} share {len(lands)}")
    rest = sum(len(lands) for _, lands in order[MAX_GROUPS:])
    if rest:
        parts.append(f"others own {rest} more")
    listed = _and(parts) if len(parts) < 3 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    cited = [p.name for land in records for p in land.owning()]
    text = f"You didn't say whose, so here is everyone in your documents who owns {where}: {listed}."
    return (text, cited), [land for _, lands in order for land in lands]


def _share_lines(land: _Land, unowned: bool) -> list[tuple[str, Cited]]:
    """Each share exactly as printed, cited; the unowned share only when printed, never derived."""
    owning = land.owning()
    shares = [p.share for p in owning if p.share is not None]
    role = "buyers" if owning and all(p.role == "buyers" for p in owning) else "owners"
    if shares:
        held = _and([clean_text(share.value_text) for share in shares])
        lines: list[tuple[str, Cited]] = [(f"{_called(land)} lists {role} holding {held}.", list(shares))]
    else:
        lines = [(f"{_called(land)} prints no share for its {role}.", [p.name for p in owning])]
    if land.unowned is not None:
        printed = clean_text(land.unowned.value_text)
        lines.append((f"It prints the unowned share as {printed}.", [land.unowned]))
    elif unowned:
        spot = (shares or [p.name for p in owning])[:1]  # the page to check, cited
        check = "".join(f" Please check page {ref.page_no}." for ref in spot)
        lines.append((f"It does not print an unowned share, so none is worked out.{check}", spot))
    return lines


def _map_reply(source: PgOwners, customer_id: str, ask: OwnerAsk, scope: Scope) -> Reply | None:
    """A map question about ``ask.properties``: each record's map values as printed and cited, or
    that one asked for is not printed (never worked out); with ``borders``, also the records that
    print one of them as a neighbour. None when no record holds them."""
    own = source.keyed(customer_id, "property", ask.properties, scope)[:MAX_LISTED]
    beside: list[tuple[str, str]] = []
    if ask.borders:
        printing = source.keyed(customer_id, "property", ask.properties, scope, tuple(_SIDES))
        beside = [record for record in printing if record not in own][:MAX_LISTED]
    if not own and not beside:
        return None
    lands = _lands(source.shares(customer_id, sorted({d for d, _ in own + beside}), scope))
    sentences: list[tuple[str, Cited]] = []
    documents = []
    for record, lines in [(r, _map_lines) for r in own] + [(r, _beside_lines) for r in beside]:
        said = lines(lands[record], ask) if record in lands else []
        if said:
            sentences += said
            document_id, title, why, cited = _document(lands[record], ())
            cited += [ref for _, refs in said for ref in refs if ref not in cited]
            documents.append((document_id, title, why or "values printed on its map", cited))
    return Reply(True, sentences, documents, []) if sentences else None


def _map_lines(land: _Land, ask: OwnerAsk) -> list[tuple[str, Cited]]:
    """'The length of Survey 45/2 is printed as 45.5 m.', 'The width of Survey 45/2 is not printed
    on the map, so none is worked out.', its neighbours by side, or every value its map prints."""
    name, named = _named(land)
    lines: list[tuple[str, Cited]] = []
    for stored in ask.measures:
        refs = [ref for ref, _ in land.printed.get(stored, [])]
        word, verb = _MEASURES[stored], "are" if stored == "land_dimension" else "is"
        if refs:
            printed = _and([clean_text(ref.value_text) for ref in refs])
            lines.append((f"The {word} of {name} {verb} printed as {printed}.", [*named, *refs]))
        else:
            lines.append(
                (f"The {word} of {name} {verb} not printed on the map, so none is worked out.", named)
            )
    if ask.borders:
        sides = [(side, ref) for stored, side in _SIDES.items() for ref, _ in land.printed.get(stored, [])]
        if sides:
            listed = _and([f"{clean_text(ref.value_text)} on the {side}" for side, ref in sides])
            lines.append((f"The neighbours printed for {name}: {listed}.", [*named, *(r for _, r in sides)]))
        else:
            lines.append((f"No neighbour of {name} is printed by side, so none is worked out.", named))
    if not lines:  # the map itself
        words = {**_MEASURES, **{stored: f"{side} neighbour" for stored, side in _SIDES.items()}}
        parts = [
            (word, [r for r, _ in land.printed[stored]])
            for stored, word in words.items()
            if stored in land.printed
        ]
        if parts:
            listed = "; ".join(
                f"{word} {_and([clean_text(r.value_text) for r in refs])}" for word, refs in parts
            )
            cited = [*named, *(ref for _, refs in parts for ref in refs)]
            lines.append((f"The map values printed for {name}: {listed}.", cited))
        else:
            lines.append((f"No value is printed on a map of {name}, so none is worked out.", named))
    return lines


def _beside_lines(land: _Land, ask: OwnerAsk) -> list[tuple[str, Cited]]:
    """'Survey 46 prints 45/2 as its west neighbour.'"""
    name, named = _named(land)
    sides = [
        (side, ref)
        for stored, side in _SIDES.items()
        for ref, latin in land.printed.get(stored, [])
        if latin in ask.properties
    ]
    if not sides:
        return []
    listed = _and([f"{clean_text(ref.value_text)} as its {side} neighbour" for side, ref in sides])
    return [(f"{name[:1].upper()}{name[1:]} prints {listed}.", [*named, *(ref for _, ref in sides)])]


def _named(land: _Land) -> tuple[str, Cited]:
    """A record named by its survey and plot numbers as printed, with the values that name it."""
    plot = None if land.plot is None else clean_text(land.plot.value_text)
    if land.survey is not None:
        name = f"Survey {clean_text(land.survey.value_text)}" + ("" if plot is None else f", plot {plot}")
    else:
        name = "this land record" if plot is None else f"Plot {plot}"
    return name, [ref for ref in (land.survey, land.plot) if ref is not None]


def _document(land: _Land, people: Sequence[str]) -> tuple[str, str, str, Cited]:
    """A listed record: its title, why it matched in words (owners, places; no digits), and its
    values: survey number, places, the owners named, their shares and the unowned share as printed."""
    owning = land.owning()
    hidden = _hidden(land, people)
    shown = sorted(
        (p for p in owning if p not in hidden),
        key=lambda p: people.index(p.person) if p.person in people else len(people),
    )
    role = "buyers" if owning and all(p.role == "buyers" for p in owning) else "owners"
    parts = []
    names = [_name(p) for p in shown if _plain(_name(p))]
    if names:
        parts.append(f"{role} {', '.join(names)}" + (" and others" if hidden else ""))
    if owning and all(p.share is None for p in owning):
        parts.append("shares not printed")
    places = [clean_text(ref.value_text) for ref, _ in land.places if _plain(ref.value_text)]
    why = "; ".join(filter(None, (", ".join(parts), ", ".join(dict.fromkeys(places)))))
    cited = [] if land.survey is None else [land.survey]
    cited += [ref for ref, _ in land.places] + [p.name for p in shown]
    cited += [p.share for p in shown if p.share is not None]
    cited += [] if land.unowned is None else [land.unowned]
    return land.document_id, land.title, why, cited


def _hidden(land: _Land, people: Sequence[str]) -> list[_Party]:
    """A record's owners past MAX_NAMED that were not asked for: counted, not named on its line."""
    owning = land.owning()
    if len(owning) <= MAX_NAMED:
        return []
    keep = [p for p in owning if p.person in people] or owning[:MAX_NAMED]
    return [p for p in owning if p not in keep]


def _called(land: _Land) -> str:
    """A sentence names a record by its survey number as printed (cited on its line)."""
    survey = None if land.survey is None else clean_text(land.survey.value_text)
    return "The land record" if survey is None else f"The record for Survey {survey}"


def _name(party: _Party) -> str:
    return " ".join(clean_text(party.name.value_text).split())


def _plain(words: str) -> bool:
    """No digit in it: a record's line carries words only (decision 2)."""
    return not any(ch.isdigit() for ch in words)


def _and(items: Sequence[str], word: str = "and") -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}"
