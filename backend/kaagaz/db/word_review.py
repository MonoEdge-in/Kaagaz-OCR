"""Word-level review (scan review UX rules, Tushar, 16 Oct 2026; the council plan of 16 Oct
2026, PRs 2 to 4): the flagged words of a document with their state, a person's Confirm and
Edit, Undo, the counts of the summary bar, and the changes written again when the document
is saved again.

Why here: the SQL in one reviewable place. Every function takes a connection from
``Database.customer(customer_id)`` and filters on ``customer_id`` as well (two locks).

* **Flags are derived** on every request (``load``), never stored: one pass over the first
  reader's stored spans joined to their pages (the shape of ``listing._PAGE_MARKS``), and
  the document's flagged values; ``kaagaz.extraction.flags`` decides band, reason, score
  and alternatives. A word's state is its latest change still in force (``word_changes``,
  0030): open, reviewed (confirm or edit) or resolved (Ensemble, later). A value a change
  verified (label reason ``corrected``) stays listed, reviewed, with the band and reason it
  had (kept in the change's ``applied``).
* **Confirm and Edit** (``change_word``) run in one transaction under the family lock
  (``trash.lock_family``, the save's lock too, so a read again never interleaves): the
  document must be ready, current and out of Trash (``not_ready``, ``in_trash``), the word
  still flagged (``not_found``) and still showing the text the person saw
  (``changed_since``). An edit is written into the values and search pieces whose box
  holds the word, and only where the old text occurs exactly once in them: never a guess.
  A piece's vector is cleared, so the worker's EmbedMissing makes it again. A value is
  Verified only when every flag in its box was confirmed or edited by a person, through
  the correction screen's own path (``review.resolve_value``); a value with a reason no
  flag explains (check_failed, single_reader, model_chosen) keeps its label. What was
  written, with what it replaced, goes in ``applied``.
* **Undo** (``undo_change``) is for the document's latest change in force only
  (``not_latest`` otherwise), so undoing in turn walks back exactly; it puts back only rows
  still as the change left them (``changed_since`` otherwise, nothing changed). A row a read
  again replaced is gone, and is left out.
* **Saved again** (``reapply``, called by ``PgPipelineStore.save`` after the values): each
  change in force, oldest first, whose word is still on the page is written again into the
  new rows, so a read again of another page never undoes a person's text silently. A word
  no longer on the page stays in the history (``on_page`` false).

Audit rows hold ids, codes and counts only, never a word. Cost: ``load`` is O(s) in the
document's stored spans (unpacked in the database, only the flagged ones back) plus O(v)
values and O(c) changes; a change adds O(values + pieces of one page).
ponytail: the whole history is read per request; keyset paging in SQL if one document
gathers thousands of changes.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, RowMapping, TextClause, text

from kaagaz.chunking.script import is_devanagari, latin_spelling
from kaagaz.db import audit
from kaagaz.db import review as review_sql
from kaagaz.db.trash import lock_family
from kaagaz.extraction.flags import (
    COVERED_REASONS,
    VALUE,
    Flag,
    occurrences,
    overlaps,
    span_flag,
    value_flag,
    value_key,
)
from kaagaz.extraction.parsing import parse_amount, parse_date

PERSON_ACTIONS = ("confirm", "edit")
ENSEMBLE_ACTIONS = ("resolve", "keep")
OPEN, REVIEWED, RESOLVED = "open", "reviewed", "resolved"

# The first reader's spans read by OCR that may be flagged, with their page's reading
# result: word marks, lines that are unpinned or on a backup-read or unreadable page, and
# lines a run of Ensemble on the whole document read differently (``ensemble``: a reading
# changed or unresolved at the same spot and text). flags.span_flag decides; this is a
# superset of what it keeps.
# ponytail: the readings are looked up per span; fine while a document has few unsettled ones.
_FLAG_SPANS = text(
    "SELECT e.span ->> 'kind' AS kind, e.span ->> 'quality' AS quality,"
    " e.span ->> 'box_source' AS box_source,"
    " e.span ->> 'text' AS text, e.span -> 'region' AS region,"
    " CAST(e.span ->> 'confidence' AS double precision) AS confidence,"
    " p.page_no, p.read_quality, p.read_issues, u.ensemble"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " JOIN pages p ON p.customer_id = ro.customer_id AND p.document_id = ro.document_id"
    " AND p.page_no = CAST(e.span -> 'region' ->> 'page' AS integer)"
    " CROSS JOIN LATERAL (SELECT e.span ->> 'kind' IS NOT NULL AND EXISTS (SELECT 1 FROM ensemble_readings r"
    " WHERE r.customer_id = ro.customer_id AND r.document_id = ro.document_id AND r.page_no = p.page_no"
    " AND r.outcome IN ('changed', 'unresolved') AND r.before_text = e.span ->> 'text'"
    " AND r.region = e.span -> 'region') AS ensemble) u"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
    " AND e.span ->> 'origin' = 'ocr' AND e.span -> 'region' ->> 'kind' = 'box'"
    " AND ((e.span ->> 'kind' IS NULL AND e.span ->> 'quality' IN ('unsure', 'illegible'))"
    " OR e.span ->> 'box_source' = 'model' OR p.read_quality = 'unreadable'"
    " OR p.read_issues && CAST(ARRAY['read_locally', 'reader_refused'] AS text[]) OR u.ensemble)"
    " ORDER BY p.page_no, e.ord"
)
# A value (alias ``v``) flags.value_flag flags while open: one definition of "flagged" for this
# screen and the file list's ``values_to_review`` (kaagaz/db/listing.py).
FLAGGED_VALUE = (
    "(v.label = 'needs_review'"
    " AND (v.label_reason IN ('readers_disagree', 'low_confidence', 'ocr_script')"
    " OR (v.votes IS NOT NULL AND v.votes ->> 'value' IS NULL)))"
)
# The values that may be flagged (flags.value_flag decides), with their page's issues.
_FLAG_VALUES = text(
    "SELECT v.id, v.page_no, v.region, v.value_text, v.value_raw, v.second_reading, v.confidence,"  # noqa: S608 - a fixed fragment
    " v.label, v.label_reason, v.votes, p.read_issues"
    " FROM extracted_values v JOIN pages p ON p.customer_id = v.customer_id"
    " AND p.document_id = v.document_id AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c AND v.document_id = :d AND (v.label_reason = 'corrected'"
    f" OR {FLAGGED_VALUE})"
    " ORDER BY v.page_no, v.id"
)
# The words of the first reader's text, word marks left out (they repeat a line's words).
_WORDS = text(
    r"SELECT coalesce(sum(cardinality(regexp_split_to_array(btrim(e.span ->> 'text'), '\s+'))), 0)"
    " FROM reader_outputs ro CROSS JOIN LATERAL jsonb_array_elements(ro.output) AS e(span)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
    " AND btrim(e.span ->> 'text') <> ''"
    " AND NOT (e.span ->> 'kind' IS NULL AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
)
_PAGES = text("SELECT count(*) FROM pages WHERE customer_id = :c AND document_id = :d")
_CHANGES = text(
    "SELECT w.id, w.word_key, w.kind, w.page_no, w.region, w.as_read, w.action, w.before_text,"
    " w.after_text, w.user_id, w.applied, w.created_at, w.undone_at, w.undone_by,"
    " u.display_name AS user_name"
    " FROM word_changes w LEFT JOIN users u ON u.customer_id = w.customer_id AND u.id = w.user_id"
    " WHERE w.customer_id = :c AND w.document_id = :d ORDER BY w.created_at, w.id"
)
_DOCUMENT_STATE = text(
    "SELECT status, deleted_at, is_current FROM documents WHERE customer_id = :c AND id = :d"
)
# Under the family lock, so the times of one document's changes strictly increase even
# when the clock stands still or steps back.
_INSERT_CHANGE = text(
    "INSERT INTO word_changes (customer_id, document_id, word_key, kind, page_no, region, as_read, action,"
    " before_text, after_text, reading_id, user_id, applied, created_at)"
    " VALUES (:c, :d, :key, :kind, :page_no, CAST(:region AS jsonb), :as_read, :action, :before, :after, :r,"
    " :u, CAST(:applied AS jsonb), greatest(CAST(:now AS timestamptz), (SELECT max(w.created_at)"
    " + interval '1 microsecond' FROM word_changes w WHERE w.customer_id = :c AND w.document_id = :d)))"
    " RETURNING id"
)
_SET_APPLIED = text(
    "UPDATE word_changes SET applied = CAST(:applied AS jsonb) WHERE customer_id = :c AND id = :id"
)
_UNDO = text(
    "UPDATE word_changes SET undone_at = :now, undone_by = :u WHERE customer_id = :c AND id = :id"
    " AND undone_at IS NULL"
)
_PAGE_VALUES = text(
    "SELECT id, document_id, field, value_text, value_raw, second_reading, label, label_reason, page_no,"
    " region, value_number, value_date FROM extracted_values"
    " WHERE customer_id = :c AND document_id = :d AND page_no = :p ORDER BY id FOR UPDATE"
)
_PAGE_PIECES = text(
    "SELECT id, text, text_latin, regions FROM pieces WHERE customer_id = :c AND document_id = :d"
    " AND page_from <= :p AND page_to >= :p ORDER BY seq FOR UPDATE"
)
_VALUES_BY_ID = text(
    "SELECT id, value_text, label, label_reason FROM extracted_values"
    " WHERE customer_id = :c AND document_id = :d AND id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR UPDATE"
)
_PIECES_BY_ID = text(
    "SELECT id, text FROM pieces WHERE customer_id = :c AND document_id = :d"
    " AND id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR UPDATE"
)
# Keep (Ensemble): the value keeps the readings that changed it (extracted_values.votes, 0026).
_SET_VOTES = text(
    "UPDATE extracted_values SET votes = CAST(:votes AS jsonb) WHERE customer_id = :c AND id = :v"
)
_SET_VALUE_TEXT = text(
    "UPDATE extracted_values SET value_text = :t, value_number = :n, value_date = :dt"
    " WHERE customer_id = :c AND id = :v"
)
_RESTORE_VALUE = text(
    "UPDATE extracted_values SET value_text = :t, value_number = CAST(:n AS numeric),"
    " value_date = CAST(:dt AS date), label = :label, label_reason = :reason"
    " WHERE customer_id = :c AND id = :v"
)
# A piece's vector is cleared with its text (both NULL together), so EmbedMissing makes it again.
_SET_PIECE_TEXT = text(
    "UPDATE pieces SET text = :t, text_latin = :latin, embedding = NULL, embedding_model = NULL"
    " WHERE customer_id = :c AND id = :id"
)


class Refused(Exception):
    """A change or undo that can't be made; ``code`` is the API's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Review:
    """A document's flags, in page order, and its word changes, oldest first."""

    flags: list[Flag]
    changes: Sequence[RowMapping]

    def latest(self) -> dict[str, RowMapping]:
        """Each word's latest change still in force, by word key."""
        return {str(row["word_key"]): row for row in self.changes if row["undone_at"] is None}

    def flag(self, key: str) -> Flag | None:
        return next((flag for flag in self.flags if flag.key == key), None)


def load(conn: Connection, customer_id: str, document_id: str) -> Review:
    """The document's flags and changes (see the module docstring)."""
    ids = {"c": customer_id, "d": document_id}
    changes = conn.execute(_CHANGES, ids).mappings().all()
    latest = Review([], changes).latest()
    found: dict[str, Flag] = {}
    for flag in map(span_flag, conn.execute(_FLAG_SPANS, ids).mappings()):
        if flag is not None:
            found.setdefault(flag.key, flag)
    for row in conn.execute(_FLAG_VALUES, ids).mappings():
        settled = _settled(latest.get(value_key(row))) if row["label_reason"] == "corrected" else None
        flag = value_flag(row, settled)
        if flag is not None:
            found.setdefault(flag.key, flag)
    return Review(list(found.values()), changes)


def latest_changes(conn: Connection, customer_id: str, document_id: str) -> dict[str, RowMapping]:
    """``Review.latest`` without the flags: one statement."""
    return Review([], conn.execute(_CHANGES, {"c": customer_id, "d": document_id}).mappings().all()).latest()


def state(flag: Flag, latest: Mapping[str, RowMapping]) -> str:
    change = latest.get(flag.key)
    if change is None:
        return OPEN
    return RESOLVED if change["action"] in ENSEMBLE_ACTIONS else REVIEWED


def current_text(flag: Flag, latest: Mapping[str, RowMapping]) -> str:
    """The word's text now: a value's stored text; else its latest change's, else as read."""
    change = latest.get(flag.key)
    return flag.text if flag.kind == VALUE or change is None else str(change["after_text"])


def counts(conn: Connection, customer_id: str, document_id: str, review: Review) -> dict[str, Any]:
    """The summary bar's numbers (spec sections 3 and 7). Two statements more."""
    ids = {"c": customer_id, "d": document_id}
    latest = review.latest()
    states = [state(flag, latest) for flag in review.flags]
    times = [row["created_at"] for row in review.changes] + [
        row["undone_at"] for row in review.changes if row["undone_at"] is not None
    ]
    return {
        "pages": int(conn.execute(_PAGES, ids).scalar_one()),
        "words": int(conn.execute(_WORDS, ids).scalar_one()),
        "flagged": len(review.flags),
        "by_band": {band: sum(flag.band == band for flag in review.flags) for band in ("check", "review")},
        "reviewed": states.count(REVIEWED),
        "resolved": states.count(RESOLVED),
        "remaining": states.count(OPEN),
        "last_change_at": max(times) if times else None,
    }


def change_word(
    conn: Connection,
    customer_id: str,
    document_id: str,
    key: str,
    *,
    expected_text: str,
    new_text: str | None,
    user_id: str,
    now: datetime,
    reading_id: str | None = None,
    votes: Mapping[str, Any] | None = None,
) -> str:
    """Confirm (``new_text`` None, or the same text) or edit one flagged word, and return
    the change's id. Raises Refused (not_found, in_trash, not_ready, changed_since).
    With ``reading_id`` it is Ensemble's Keep of that reading's ``new_text``: action keep,
    written the same way but never Verifying a value nor writing into a Verified one, and a
    value flag's own value keeps ``votes`` (kaagaz.db.ensemble.keep)."""
    ids = {"c": customer_id, "d": document_id}
    lock_ready(conn, ids)
    review = load(conn, customer_id, document_id)
    flag = review.flag(key)
    if flag is None:
        raise Refused("not_found")
    latest = review.latest()
    before = current_text(flag, latest)
    if before != expected_text:
        raise Refused("changed_since")
    after = before if new_text is None else new_text
    action = "keep" if reading_id is not None else "confirm" if after == before else "edit"
    change_id = str(
        conn.execute(
            _INSERT_CHANGE,
            {
                **ids,
                "key": key,
                "kind": flag.kind,
                "page_no": flag.page_no,
                "region": json.dumps(flag.region),
                "as_read": flag.as_read,
                "action": action,
                "before": before,
                "after": after,
                "r": reading_id,
                "u": user_id,
                "applied": "{}",
                "now": now,
            },
        ).scalar_one()
    )
    person = reading_id is None
    reviewed = {word for word, row in latest.items() if row["action"] in PERSON_ACTIONS}
    if person:
        reviewed.add(key)
    applied = _apply(
        conn, ids, flag, before, after, review.flags, reviewed, user_id, change_id, verify=person, votes=votes
    )
    applied["flag"] = {"band": flag.band, "reason": flag.reason}
    conn.execute(_SET_APPLIED, {"c": customer_id, "id": change_id, "applied": json.dumps(applied)})
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="ensemble.keep" if reading_id is not None else f"word_review.{action}",
        target_type="document",
        target_id=document_id,
        details=_audit_details(change_id, flag, applied),
    )
    return change_id


def undo_change(
    conn: Connection, customer_id: str, document_id: str, change_id: str, *, user_id: str, now: datetime
) -> None:
    """Undo the document's latest change in force. Raises Refused (not_found, in_trash,
    not_ready, not_latest, changed_since); nothing is changed then."""
    ids = {"c": customer_id, "d": document_id}
    lock_ready(conn, ids)
    changes = conn.execute(_CHANGES, ids).mappings().all()
    live = [row for row in changes if row["undone_at"] is None]
    change = next((row for row in changes if str(row["id"]) == change_id), None)
    if change is None:
        raise Refused("not_found")
    if not live or str(live[-1]["id"]) != change_id:
        raise Refused("not_latest")
    applied = change["applied"] or {}
    values = {str(row["id"]): row for row in _rows(conn, _VALUES_BY_ID, ids, applied.get("values", []))}
    pieces = {str(row["id"]): row for row in _rows(conn, _PIECES_BY_ID, ids, applied.get("pieces", []))}
    for entry in applied.get("values", []):
        row = values.get(entry["id"])
        if row is not None and (
            row["value_text"] != entry["after"]
            or (entry["verified"] and (row["label"], row["label_reason"]) != ("verified", "corrected"))
        ):
            raise Refused("changed_since")
    for entry in applied.get("pieces", []):
        row = pieces.get(entry["id"])
        if row is not None and row["text"] != entry["after"]:
            raise Refused("changed_since")
    for entry in applied.get("values", []):
        if entry["id"] in values:
            before = entry["before"]
            conn.execute(
                _RESTORE_VALUE,
                {
                    "c": customer_id,
                    "v": entry["id"],
                    "t": before["text"],
                    "n": before["number"],
                    "dt": before["date"],
                    "label": before["label"],
                    "reason": before["reason"],
                },
            )
    for entry in applied.get("pieces", []):
        if entry["id"] in pieces:
            params = {"c": customer_id, "id": entry["id"], "t": entry["before"], "latin": entry["latin"]}
            conn.execute(_SET_PIECE_TEXT, params)
    conn.execute(_UNDO, {"c": customer_id, "id": change_id, "now": now, "u": user_id})
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="word_review.undo",
        target_type="document",
        target_id=document_id,
        details={"change_id": change_id, "values": len(values), "pieces": len(pieces)},
    )


def reapply(conn: Connection, customer_id: str, document_id: str) -> None:
    """Write each change in force again into the rows a save has just inserted, oldest
    first, for the words still on the page (see the module docstring). In the save's
    transaction, under its family lock. One statement when the document has no change."""
    ids = {"c": customer_id, "d": document_id}
    live = [row for row in conn.execute(_CHANGES, ids).mappings() if row["undone_at"] is None]
    if not live:
        return
    review = load(conn, customer_id, document_id)
    flags = {flag.key: flag for flag in review.flags}
    reviewed: set[str] = set()
    for change in live:
        flag = flags.get(str(change["word_key"]))
        if flag is None or change["user_id"] is None:
            continue  # no longer on the page: it stays in the history
        if change["action"] in PERSON_ACTIONS:
            reviewed.add(flag.key)
        before, after = str(change["before_text"]), str(change["after_text"])
        change_id = str(change["id"])
        verify = change["action"] in PERSON_ACTIONS  # a Keep never Verifies (Ensemble)
        applied = _apply(
            conn,
            ids,
            flag,
            before,
            after,
            review.flags,
            reviewed,
            str(change["user_id"]),
            change_id,
            verify=verify,
        )
        applied["flag"] = (change["applied"] or {}).get("flag")
        conn.execute(_SET_APPLIED, {"c": customer_id, "id": change_id, "applied": json.dumps(applied)})


def resolve_word(
    conn: Connection,
    customer_id: str,
    document_id: str,
    flag: Flag,
    text_now: str,
    reading_id: str,
    now: datetime,
) -> str:
    """Ensemble's readings agreed with the word's text ``text_now``: a ``resolve`` change by no
    person, which writes nothing (the text stays), and its id. The caller holds the family lock
    (kaagaz.db.ensemble.save_page)."""
    params = {
        "c": customer_id,
        "d": document_id,
        "key": flag.key,
        "kind": flag.kind,
        "page_no": flag.page_no,
        "region": json.dumps(flag.region),
        "as_read": flag.as_read,
        "action": "resolve",
        "before": text_now,
        "after": text_now,
        "r": reading_id,
        "u": None,
        "applied": "{}",
        "now": now,
    }
    return str(conn.execute(_INSERT_CHANGE, params).scalar_one())


def lock_ready(conn: Connection, ids: dict[str, str]) -> None:
    """Take the family lock, then refuse a document that is not there, in Trash, or not
    ready and current."""
    if lock_family(conn, ids["c"], ids["d"]) is None:
        raise Refused("not_found")
    row = conn.execute(_DOCUMENT_STATE, ids).one()
    if row.deleted_at is not None:
        raise Refused("in_trash")
    if row.status != "ready" or not row.is_current:
        raise Refused("not_ready")


def _apply(
    conn: Connection,
    ids: dict[str, str],
    flag: Flag,
    before: str,
    after: str,
    flags: Sequence[Flag],
    reviewed: set[str],
    user_id: str,
    change_id: str,
    *,
    verify: bool = True,
    votes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one change into the values and pieces of the word's page, and verify each
    value whose box flags are all reviewed. Returns what was written (``applied``). With
    ``verify`` False (a Keep) no value is Verified and a Verified one is left as it is;
    ``votes`` go on the flag's own value."""
    params = {**ids, "p": flag.page_no}
    written_values = []
    for value in conn.execute(_PAGE_VALUES, params).mappings().all():
        mine = str(value["id"]) == flag.value_id
        if not (mine or (flag.kind != VALUE and overlaps(flag.region, value["region"]))):
            continue
        text_now = str(value["value_text"])
        new: str | None = None
        if mine and after != text_now:
            new = after
        elif not mine and before != after and occurrences(text_now, before) == 1:
            new = text_now.replace(before, after, 1)
        box = [f for f in flags if f.value_id == str(value["id"]) or overlaps(f.region, value["region"])]
        ready = (
            value["label"] == "needs_review"
            and value["label_reason"] in COVERED_REASONS
            and bool(box)
            and all(f.key in reviewed for f in box)
        )
        if not verify and value["label"] == "verified":
            continue  # Ensemble's Keep never changes a value a person Verified
        verified = verify and (ready or (value["label"] == "verified" and new is not None))
        if not verified and new is None:
            continue
        final = text_now if new is None else new
        if verified:
            review_sql.resolve_value(
                conn,
                ids["c"],
                value,
                new_text=final,
                choice=_choice(final, value),
                remember=False,
                user_id=user_id,
                details={"word_change": change_id},
            )
        else:
            conn.execute(
                _SET_VALUE_TEXT,
                {**ids, "v": str(value["id"]), "t": final, "n": parse_amount(final), "dt": parse_date(final)},
            )
            if mine and votes is not None:
                conn.execute(_SET_VOTES, {**ids, "v": str(value["id"]), "votes": json.dumps(votes)})
        entry = {"id": str(value["id"]), "before": _value_before(value), "after": final, "verified": verified}
        written_values.append(entry)
    written_pieces = []
    if before != after:
        for piece in conn.execute(_PAGE_PIECES, params).mappings().all():
            old = str(piece["text"])
            if (
                not any(overlaps(flag.region, region) for region in piece["regions"])
                or occurrences(old, before) != 1
            ):
                continue
            new_piece = old.replace(before, after, 1)
            latin = latin_spelling(new_piece) if is_devanagari(new_piece) else None
            conn.execute(_SET_PIECE_TEXT, {**ids, "id": str(piece["id"]), "t": new_piece, "latin": latin})
            written_pieces.append(
                {"id": str(piece["id"]), "before": old, "latin": piece["text_latin"], "after": new_piece}
            )
    return {"values": written_values, "pieces": written_pieces}


def _choice(final: str, value: Mapping[Any, Any]) -> str:
    """How the correction screen would name this text: the first reading, the second, or typed."""
    if final == value["value_raw"]:
        return "first"
    return "second" if final == value["second_reading"] else "typed"


def _value_before(value: Mapping[Any, Any]) -> dict[str, Any]:
    number, day = value["value_number"], value["value_date"]
    return {
        "text": value["value_text"],
        "number": str(number) if isinstance(number, Decimal) else None,
        "date": day.isoformat() if isinstance(day, date) else None,
        "label": value["label"],
        "reason": value["label_reason"],
    }


def _settled(change: RowMapping | None) -> tuple[str, str] | None:
    """The (band, reason) a value had when ``change`` verified it, from its ``applied``."""
    flag = (change["applied"] or {}).get("flag") if change is not None else None
    if isinstance(flag, dict) and isinstance(flag.get("band"), str) and isinstance(flag.get("reason"), str):
        return flag["band"], flag["reason"]
    return None


def _rows(
    conn: Connection, statement: TextClause, ids: dict[str, str], entries: Sequence[Any]
) -> Sequence[RowMapping]:
    wanted = sorted({str(entry["id"]) for entry in entries})
    return conn.execute(statement, {**ids, "ids": wanted}).mappings().all() if wanted else []


def _audit_details(change_id: str, flag: Flag, applied: Mapping[str, Any]) -> dict[str, audit.DetailValue]:
    values = applied["values"]
    return {
        "change_id": change_id,
        "kind": flag.kind,
        "page_no": flag.page_no,
        "values": len(values),
        "verified": sum(bool(entry["verified"]) for entry in values),
        "pieces": len(applied["pieces"]),
    }
