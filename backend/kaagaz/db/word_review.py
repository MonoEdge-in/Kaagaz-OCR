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
* **Listed** (#432, Tushar, 28 Oct 2026): a person is asked to review the ``review`` band
  only (``Review.listed``: the list, every count and Ensemble on flagged words). A ``check``
  word (a second look is wise) is no review item: its reading-problem mark stays on the page,
  and ``load`` still finds it, so a change already made to it is kept and written again.
* **A page settled through its flags** (#443, Tushar, 28 Oct 2026): a person's change that
  settles the last review item of a page with problems, every one of them one a flag stands
  for (``FLAGGED_ISSUES``: a [?], a line not pinned, a value read differently), keeps that
  page as is (F5, ``documents.accept_page``) at the change's time, so it leaves Needs
  attention and the file list's attention with its last word; ``applied`` says so
  (``kept``), and Undo puts the page back. A page with another problem (faint, blurry...)
  or none (uncertain only for its unsure words) waits for Keep as is. The Needs attention
  count follows the same rule (frontend/components/viewer/pageProblems.ts, toSettle).
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
* **A line of the clean copy** (#527, Tushar, 7 Nov 2026): a reviewer may correct any line
  of the main reader (``lines``, the clean copy's own), flagged or not, through
  ``change_word`` with ``line``; its key is a flagged line's (``line_key``), so a flagged
  line is one item in both places. Written as an edit is, over the line's text now (its
  word edits too, ``line_text``), and a value on the line (``_inside``) whose text is a part
  of it takes the change too when it falls inside that part, there once (``line_part``).
  What was not written is counted (``applied["skipped"]``). The stored reading is never
  changed: the clean copy shows the text now and the correction in force
  (``line_correction``). Its audit rows are ``document.line_corrected`` and
  ``document.line_correction_undone``.

Audit rows hold ids, codes and counts only, never a word. Cost: ``load`` is O(s) in the
document's stored spans (unpacked in the database, only the flagged ones back) plus O(v)
values and O(c) changes; a change adds O(values + pieces of one page).
ponytail: the whole history is read per request; keyset paging in SQL if one document
gathers thousands of changes.
"""

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, RowMapping, TextClause, text

from kaagaz.api import names
from kaagaz.chunking.script import piece_latin
from kaagaz.db import audit, listing, value_keys
from kaagaz.db import review as review_sql
from kaagaz.db.documents import accept_page
from kaagaz.db.trash import lock_family
from kaagaz.db.visibility import SEEN_DOCUMENT
from kaagaz.extraction.flags import (
    COVERED_REASONS,
    LINE,
    MARK,
    REVIEW,
    VALUE,
    Flag,
    centre_in,
    occurrences,
    overlaps,
    span_flag,
    value_flag,
    value_key,
    word_key,
)
from kaagaz.extraction.land import value_number
from kaagaz.extraction.parsing import parse_date
from kaagaz.reading.page_quality import ILLEGIBLE_WORDS, READERS_DISAGREE, UNPINNED

PERSON_ACTIONS = ("confirm", "edit")
ENSEMBLE_ACTIONS = ("resolve", "keep")
OPEN, REVIEWED, RESOLVED = "open", "reviewed", "resolved"
# The page problems a review item stands for (#443, see the module docstring). Pinned to the
# frontend's copy by frontend/tests/attention-settle-443.test.ts.
FLAGGED_ISSUES = frozenset({ILLEGIBLE_WORDS, UNPINNED, READERS_DISAGREE})

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
# A value (alias ``v``) flags.value_flag flags while open: this screen's flagged values, some
# of the values still waiting to be verified (review.WAITING_VALUE), which the file list
# counts (QA #366).
FLAGGED_VALUE = (
    f"({review_sql.WAITING_VALUE}"
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
    r"SELECT coalesce(sum(cardinality(regexp_split_to_array(btrim(e.span ->> 'text'), '\s+'))), 0)"  # noqa: S608 - a fixed fragment, visibility.SEEN_DOCUMENT
    " FROM reader_outputs ro CROSS JOIN LATERAL jsonb_array_elements(ro.output) AS e(span)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
    + SEEN_DOCUMENT
    + " AND btrim(e.span ->> 'text') <> ''"
    " AND NOT (e.span ->> 'kind' IS NULL AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
)
_PAGES = text("SELECT count(*) FROM pages WHERE customer_id = :c AND document_id = :d" + SEEN_DOCUMENT)  # noqa: S608 - a fixed fragment, visibility.SEEN_DOCUMENT
_CHANGES = text(
    "SELECT w.id, w.word_key, w.kind, w.page_no, w.region, w.as_read, w.action, w.before_text,"
    " w.after_text, w.user_id, w.applied, w.created_at, w.undone_at, w.undone_by,"
    " u.display_name AS user_name"
    " FROM word_changes w LEFT JOIN users u ON u.customer_id = w.customer_id AND u.id = w.user_id"
    " WHERE w.customer_id = :c AND w.document_id = :d ORDER BY w.created_at, w.id"
)
# A page on the attention list (not good, not kept as is), with its problems.
_PAGE_TO_KEEP = text(
    "SELECT read_issues FROM pages WHERE customer_id = :c AND document_id = :d AND page_no = :p"
    " AND read_quality <> 'good' AND accepted_at IS NULL"
)
# Undo of a page a change kept: only while it is still kept from that change (its time).
_UNKEEP_PAGE = text(
    "UPDATE pages SET accepted_by = NULL, accepted_at = NULL WHERE customer_id = :c AND document_id = :d"
    " AND page_no = :p AND accepted_at = :at"
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
    " RETURNING id, created_at"
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
    "SELECT id, value_text, label, label_reason FROM extracted_values"  # noqa: S608 - a fixed fragment, visibility.SEEN_DOCUMENT
    " WHERE customer_id = :c AND document_id = :d AND id = ANY(CAST(:ids AS uuid[]))"
    + SEEN_DOCUMENT
    + " ORDER BY id FOR UPDATE"
)
_PIECES_BY_ID = text(
    "SELECT id, text FROM pieces WHERE customer_id = :c AND document_id = :d"  # noqa: S608 - a fixed fragment, visibility.SEEN_DOCUMENT
    " AND id = ANY(CAST(:ids AS uuid[]))" + SEEN_DOCUMENT + " ORDER BY id FOR UPDATE"
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

    @property
    def listed(self) -> list[Flag]:
        """The flags a person is asked to review: the ``review`` band (#432)."""
        return [flag for flag in self.flags if flag.band == REVIEW]


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
    listed = review.listed
    states = [state(flag, latest) for flag in listed]
    times = [row["created_at"] for row in review.changes] + [
        row["undone_at"] for row in review.changes if row["undone_at"] is not None
    ]
    return {
        "pages": int(conn.execute(_PAGES, ids).scalar_one()),
        "words": int(conn.execute(_WORDS, ids).scalar_one()),
        "flagged": len(listed),
        "by_band": {band: sum(flag.band == band for flag in listed) for band in ("check", "review")},
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
    line: bool = False,
) -> str:
    """Confirm (``new_text`` None, or the same text) or edit one flagged word, and return
    the change's id. Raises Refused (not_found, in_trash, not_ready, changed_since).
    With ``line`` (#527) ``key`` is a line of the clean copy, flagged or not, an edit whose
    text must differ from the line as the clean copy shows it, made one line (``unchanged``
    otherwise); it replaces the line's text now (``line_text``) in the pieces and values.
    With ``reading_id`` it is Ensemble's Keep of that reading's ``new_text``: action keep,
    written the same way but never Verifying a value nor writing into a Verified one, and a
    value flag's own value keeps ``votes`` (kaagaz.db.ensemble.keep)."""
    ids = {"c": customer_id, "d": document_id}
    lock_ready(conn, ids)
    review = load(conn, customer_id, document_id)
    flag = review.flag(key)
    latest = review.latest()
    if line:
        row = lines(conn, customer_id, document_id).get(key)
        if row is None:
            raise Refused("not_found")
        flag = flag or _line_flag(row)  # a flagged line keeps its band: settling its page works
        now_text = line_text(row, [change for change in review.changes if change["undone_at"] is None])
        before = str(row["as_read"]) if now_text is None else now_text  # what the pieces hold
        shown = str(row["text"]) if now_text is None else now_text  # what the clean copy shows
    elif flag is None:
        raise Refused("not_found")
    else:
        before = shown = current_text(flag, latest)
    if expected_text not in (before, shown):
        raise Refused("changed_since")
    after = before if new_text is None else new_text
    if line and after == names.one_line(shown):
        raise Refused("unchanged")
    # #685: a word fixed where its text is in its line more than once goes in its own place.
    place = None
    if flag.kind == MARK and after != before and reading_id is None:
        place = _place(conn, customer_id, document_id, flag, before, review, latest)
    action = "keep" if reading_id is not None else "confirm" if after == before and not line else "edit"
    inserted = conn.execute(
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
    ).one()
    change_id = str(inserted.id)
    person = reading_id is None
    reviewed = {word for word, row in latest.items() if row["action"] in PERSON_ACTIONS}
    if person:
        reviewed.add(key)
    applied = _apply(
        conn,
        ids,
        flag,
        before,
        after,
        review.flags,
        reviewed,
        user_id,
        change_id,
        verify=person,
        votes=votes,
        place=place,
    )
    if place is not None:
        applied["place"] = place  # read by line_now, and by reapply after a read again
    applied["flag"] = {"band": flag.band, "reason": flag.reason}
    if _keep_settled_page(conn, ids, flag, review.listed, latest, user_id, inserted.created_at):
        applied["kept"] = True
    if line:
        applied["line"] = True  # its Undo's audit row says so
    conn.execute(_SET_APPLIED, {"c": customer_id, "id": change_id, "applied": json.dumps(applied)})
    named = "document.line_corrected" if line else f"word_review.{action}"
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="ensemble.keep" if reading_id is not None else named,
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
    value_keys.key_values(conn, customer_id, list(values))  # the restored texts' keys (0049)
    for entry in applied.get("pieces", []):
        if entry["id"] in pieces:
            params = {"c": customer_id, "id": entry["id"], "t": entry["before"], "latin": entry["latin"]}
            conn.execute(_SET_PIECE_TEXT, params)
    kept = bool(applied.get("kept")) and (
        conn.execute(_UNKEEP_PAGE, {**ids, "p": change["page_no"], "at": change["created_at"]}).rowcount == 1
    )
    conn.execute(_UNDO, {"c": customer_id, "id": change_id, "now": now, "u": user_id})
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="document.line_correction_undone" if applied.get("line") else "word_review.undo",
        target_type="document",
        target_id=document_id,
        details={
            "change_id": change_id,
            "values": len(values),
            "pieces": len(pieces),
            **({"kept": True, "page_no": int(change["page_no"])} if kept else {}),
        },
    )


def reapply(
    conn: Connection, customer_id: str, document_id: str, *, only: Collection[str] | None = None
) -> None:
    """Write each change in force again into the rows a save has just inserted, oldest
    first, for the words still on the page (see the module docstring). In the save's
    transaction, under its family lock. One statement when the document has no change.
    ``only`` (the land facts re-read): into these values alone, never a piece, each change
    keeping what it wrote before too, so nothing already written is written twice."""
    ids = {"c": customer_id, "d": document_id}
    live = [row for row in conn.execute(_CHANGES, ids).mappings() if row["undone_at"] is None]
    if not live:
        return
    review = load(conn, customer_id, document_id)
    flags = {flag.key: flag for flag in review.flags}
    found: dict[str, RowMapping] | None = None  # the lines, read once if a change needs them
    reviewed: set[str] = set()
    for change in live:
        flag = flags.get(str(change["word_key"]))
        if flag is None and change["kind"] == LINE:
            found = lines(conn, customer_id, document_id) if found is None else found
            row = found.get(str(change["word_key"]))
            flag = None if row is None else _line_flag(row)
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
            only=only,
            place=(change["applied"] or {}).get("place"),
        )
        before_applied = change["applied"] or {}
        if only is not None:
            applied["values"] = [*before_applied.get("values", []), *applied["values"]]
            applied["pieces"] = before_applied.get("pieces", [])
            skipped = before_applied.get("skipped", {"pieces": 0, "values": 0})
            applied["skipped"] = {
                "pieces": skipped["pieces"],
                "values": skipped["values"] + applied["skipped"]["values"],
            }
        applied["flag"] = before_applied.get("flag")
        for kept in ("kept", "line", "place"):
            if kept in before_applied:
                applied[kept] = before_applied[kept]
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


def lines(conn: Connection, customer_id: str, document_id: str) -> dict[str, RowMapping]:
    """The clean copy's lines (``listing.clear_copy_lines``) by ``line_key``; of two alike
    (same page, spot and text) the first. O(spans of the document).
    shortcut: a line past the clean copy's cap (CLEAR_COPY_LINES, CLEAR_COPY_TEXT) is not here,
    so it can't be corrected and a read again keeps such a correction in the history only; lift
    the cap for this lookup if documents grow past it."""
    found: dict[str, RowMapping] = {}
    for row in listing.clear_copy_lines(conn, customer_id, document_id):
        found.setdefault(line_key(row), row)
    return found


def line_key(row: Mapping[Any, Any]) -> str:
    """A clean copy line's word key: a flagged line's (flags.span_flag), from its text as read."""
    return word_key(LINE, int(row["page_no"]), row["region"], str(row["as_read"]))


def in_force(conn: Connection, customer_id: str, document_id: str) -> list[RowMapping]:
    """The document's changes in force, oldest first: one statement."""
    changes = conn.execute(_CHANGES, {"c": customer_id, "d": document_id}).mappings().all()
    return [change for change in changes if change["undone_at"] is None]


def line_correction(
    row: Mapping[Any, Any], live: Sequence[RowMapping], word: RowMapping | None
) -> RowMapping | None:
    """The line's own latest change in force (``live``, oldest first) when it corrects the line:
    an edit, back to the text as read too, or any change to other text; else ``word``, the last
    change of a word or value inside it that ``line_now`` wrote into it (PRD 9 F-12: the line shows
    as corrected, with that change's Undo), or None."""
    key = line_key(row)
    change = next((change for change in reversed(live) if change["word_key"] == key), None)
    if change is None or (change["action"] != "edit" and change["after_text"] == row["as_read"]):
        return word
    return change


def line_text(row: Mapping[Any, Any], live: Sequence[RowMapping]) -> str | None:
    """A clean copy line's text now, which its pieces hold: as read, with each change in force
    (``live``, oldest first) that touches it, in order: its own, and a word's or a value's
    inside it (its box, or its centre) where the word's text is there once, as ``_apply`` wrote
    it. None when no change touches it (the clean copy then shows the text as the reader gave
    it). O(changes)."""
    return line_now(row, live)[0]


def line_now(row: Mapping[Any, Any], live: Sequence[RowMapping]) -> tuple[str | None, RowMapping | None]:
    """``line_text``, and the last change of a word or value inside the line that it wrote, or None.
    The line's own change sets its text only when it changes it (PRD 9 review of #683: a confirm
    of the line as read, after a word in it was fixed, writes nothing, so the fix stays)."""
    key, text_now, touched, word = line_key(row), str(row["as_read"]), False, None
    for change in live:
        before, after = str(change["before_text"]), str(change["after_text"])
        place = (change["applied"] or {}).get("place")
        if place is not None:  # #685: the word goes in its own place in its own line, no other
            new = _put(text_now, before, after, place) if place["line"] == key else None
            if new is not None:
                text_now, touched, word = new, True, change
            continue
        if change["word_key"] == key:
            if change["action"] == "edit" or before != after:
                touched = touched or change["action"] == "edit" or after != text_now
                text_now = after
        elif (
            change["kind"] != LINE
            and before != after
            and _inside(change["region"], row["region"])
            and occurrences(text_now, before) == 1
        ):
            text_now, touched, word = text_now.replace(before, after, 1), True, change
    return (text_now if touched else None), word


def on_page(conn: Connection, customer_id: str, document_id: str, review: Review) -> set[str]:
    """The keys of the changes still on the page: the flags', and the lines' when a line no
    flag stands for was changed (one statement more then)."""
    keys = {flag.key for flag in review.flags}
    if any(row["kind"] == LINE and row["word_key"] not in keys for row in review.changes):
        keys |= set(lines(conn, customer_id, document_id))
    return keys


def line_part(before: str, after: str, part: str) -> str | None:
    """``part``'s text once a line reads ``after`` instead of ``before``, when ``part`` is in
    ``before`` exactly once and every place the change could be (the same characters on both
    sides of it can move it) is inside ``part``, never reaching past its edges; else None.
    "150000" in "Total: 150000/-" made "Total: 1500000/-" is "1500000"; " only" added after it
    is no part of it. O(len(before) + len(after))."""
    if occurrences(before, part) != 1:
        return None
    start = before.index(part)
    end = start + len(part)
    head, tail = _same(before, after), _same(before[::-1], after[::-1])
    low, high = sorted((head, len(before) - tail))
    if not (start <= low and high <= end and low < end and high > start):
        return None
    new = after[start : end + len(after) - len(before)]
    return new if new.strip() else None


def _place(
    conn: Connection,
    customer_id: str,
    document_id: str,
    flag: Flag,
    before: str,
    review: Review,
    latest: Mapping[str, RowMapping],
) -> dict[str, Any] | None:
    """#685: where a word goes in its line: the line's key and its text now, and the word's rank
    (from 0) among the line's word marks with that text, left to right, so a piece holding the
    text elsewhere too, or a line holding it twice, gets it in its own place. None when that can't
    be told and the line holds the text once (it is written where it is there once, as before);
    Refused (``twice``) when a line holds it more than once: never a guess. One statement more."""
    live = [change for change in review.changes if change["undone_at"] is None]
    held = []
    for row in lines(conn, customer_id, document_id).values():
        if int(row["page_no"]) == flag.page_no and _inside(flag.region, row["region"]):
            now = line_text(row, live) or str(row["as_read"])
            if occurrences(now, before) > 0:
                held.append((row, now))
    if len(held) == 1:
        row, now = held[0]
        same = [
            each
            for each in review.flags
            if each.kind == MARK
            and each.page_no == flag.page_no
            and current_text(each, latest) == before
            and _inside(each.region, row["region"])
        ]
        same.sort(key=lambda each: float(each.region["box"][0]) if "box" in each.region else 0.0)
        keys = [each.key for each in same]
        if len(same) == occurrences(now, before) and flag.key in keys:
            return {"line": line_key(row), "text": now, "nth": keys.index(flag.key)}
    if any(occurrences(now, before) > 1 for _, now in held):
        raise Refused("twice")
    return None


def _put(text_now: str, before: str, after: str, place: Mapping[str, Any]) -> str | None:
    """#685: ``text_now`` with the word in its place (``_place``): the line's ``nth`` ``before``,
    where ``text_now`` holds the line once (a piece, or the line itself) or lies once in it around
    that word (a value on the line); else None. O(len(text_now) + len(line))."""
    line, nth = str(place["text"]), int(place["nth"])
    starts = [found.start() for found in re.finditer(f"(?={re.escape(before)})", line)] if before else []
    if nth >= len(starts):
        return None
    at = starts[nth]
    if occurrences(text_now, line) == 1:
        at += text_now.index(line)
    elif occurrences(line, text_now) == 1 and line.index(text_now) <= at <= line.index(text_now) + len(
        text_now
    ) - len(before):
        at -= line.index(text_now)
    else:
        return None
    return text_now[:at] + after + text_now[at + len(before) :]


def _same(a: str, b: str) -> int:
    """How many characters ``a`` and ``b`` start with alike."""
    return next((n for n, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))


def _line_flag(row: Mapping[Any, Any]) -> Flag:
    """A clean copy line as a flag of no band, for ``_apply``."""
    as_read = str(row["as_read"])
    return Flag(line_key(row), LINE, int(row["page_no"]), row["region"], as_read, as_read, "", "", None)


def _holds(region: Mapping[str, Any], spot: Mapping[str, Any]) -> bool:
    """``region`` covers ``spot``: a box overlapping it, or the same cell (a spreadsheet's)."""
    return region == spot or overlaps(region, spot)


def _inside(region: Mapping[str, Any], line: Mapping[str, Any]) -> bool:
    """``region`` is on the line: its box, or its centre, inside the line's box, or the same cell.
    A box that only reaches into the line's edge is on another line (#527 review)."""
    return region == line or centre_in(region, line)


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


def _keep_settled_page(
    conn: Connection,
    ids: dict[str, str],
    flag: Flag,
    listed: Sequence[Flag],
    latest: Mapping[str, RowMapping],
    user_id: str,
    at: datetime,
) -> bool:
    """Keep the flag's page as is, at the change's time ``at``, when this change settled its last
    review item and the page's problems are all ones a flag stands for (see the module
    docstring); whether it did. ``latest`` is from before the change. One statement more, three
    when it keeps."""
    if flag.band != REVIEW or any(
        other.page_no == flag.page_no and other.key != flag.key and state(other, latest) == OPEN
        for other in listed
    ):
        return False
    issues = conn.execute(_PAGE_TO_KEEP, {**ids, "p": flag.page_no}).scalar_one_or_none()
    if not issues or not FLAGGED_ISSUES.issuperset(issues):
        return False
    accept_page(conn, ids["c"], ids["d"], flag.page_no, user_id=user_id, now=at)  # there, out of Trash
    return True


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
    only: Collection[str] | None = None,
    place: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one change into the values and pieces of the word's page, and verify each
    value whose box flags are all reviewed. Returns what was written (``applied``), and how
    many values and pieces over the word were not (``skipped``, #527 review). With ``place``
    (#685, ``_place``) a word whose text is there more than once goes in its own place (``_put``). With
    ``verify`` False (a Keep) no value is Verified and a Verified one is left as it is;
    ``votes`` go on the flag's own value."""
    params = {**ids, "p": flag.page_no}
    written_values = []
    skipped = {"pieces": 0, "values": 0}  # over the word but not written: never a guess
    for value in conn.execute(_PAGE_VALUES, params).mappings().all():
        mine = str(value["id"]) == flag.value_id
        if only is not None and str(value["id"]) not in only:
            continue
        if not (mine or (flag.kind != VALUE and _holds(value["region"], flag.region))):
            continue
        text_now = str(value["value_text"])
        new: str | None = None
        if mine and after != text_now:
            new = after
        elif not mine and place is not None and before != after and occurrences(text_now, before) > 1:
            new = _put(text_now, before, after, place)
        elif not mine and before != after and occurrences(text_now, before) == 1:
            new = text_now.replace(before, after, 1)
        elif not mine and flag.kind == LINE and before != after and _inside(value["region"], flag.region):
            new = line_part(before, after, text_now)  # a value on the line, never one on the next
        box = [f for f in flags if f.value_id == str(value["id"]) or overlaps(f.region, value["region"])]
        ready = (
            value["label"] == "needs_review"
            and value["label_reason"] in COVERED_REASONS
            and bool(box)
            and all(f.key in reviewed for f in box)
        )
        if not verify and value["label"] == "verified":
            skipped["values"] += 1
            continue  # Ensemble's Keep never changes a value a person Verified
        verified = verify and (ready or (value["label"] == "verified" and new is not None))
        if not verified and new is None:
            skipped["values"] += 1
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
            number = value_number(str(value["field"]), final)  # a land share's only as a percentage
            conn.execute(
                _SET_VALUE_TEXT,
                {**ids, "v": str(value["id"]), "t": final, "n": number, "dt": parse_date(final)},
            )
            if mine and votes is not None:
                conn.execute(_SET_VOTES, {**ids, "v": str(value["id"]), "votes": json.dumps(votes)})
        entry = {"id": str(value["id"]), "before": _value_before(value), "after": final, "verified": verified}
        written_values.append(entry)
    written_pieces = []
    if before != after and only is None:
        for piece in conn.execute(_PAGE_PIECES, params).mappings().all():
            old = str(piece["text"])
            if not any(_holds(region, flag.region) for region in piece["regions"]):
                continue
            if occurrences(old, before) == 1:
                new_piece: str | None = old.replace(before, after, 1)
            else:  # #685: in its own place, else not written
                new_piece = None if place is None else _put(old, before, after, place)
            if new_piece is None:
                skipped["pieces"] += 1
                continue
            latin = piece_latin(new_piece)
            conn.execute(_SET_PIECE_TEXT, {**ids, "id": str(piece["id"]), "t": new_piece, "latin": latin})
            written_pieces.append(
                {"id": str(piece["id"]), "before": old, "latin": piece["text_latin"], "after": new_piece}
            )
    value_keys.key_values(conn, ids["c"], [str(entry["id"]) for entry in written_values])  # 0049
    return {"values": written_values, "pieces": written_pieces, "skipped": skipped}


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
        **({"kept": True} if applied.get("kept") else {}),
    }
