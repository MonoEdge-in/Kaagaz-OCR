"""Versions against the database (Phase 2 PRD 1, V1 to V12): the fingerprint, spotting a new
version, the suggestion and its answer, Undo, make current, compare and the version list.

Why here: the family rules next to their SQL. Every function takes a connection from
``Database.customer(customer_id)`` and filters on ``customer_id`` too (two locks).

* **The text** (V1, verify): the first reader's stored output (``reader_outputs``, role
  ``first``), span by span in stored order within each page, word marks left out (as the
  viewer's ``page_readings``). It is the reading as read: a person's word fixes go into the
  values and pieces only (``word_review.reapply``), the card and the map never touch it, and a
  read again of an unchanged page takes the page cache's reading (G7), so a read again
  changes the fingerprint only when the reading itself changed. A spreadsheet's spans are its
  cells. ``text_minhash`` empty (``{}``) means "looked at, under MIN_WORDS words"; NULL means
  never looked at (the one-time sweep's work).
* **Spotting** (V3 to V5, V8) runs in the worker right after filing (``PgDocuments.
  file_document``), only on a document's first ready (no earlier ready run), only while it is
  its family's only version, and only with ``version_spotting`` on for the platform and the
  organisation. The candidates are this customer's current, ready documents out of Trash in
  other families sharing a band, a similar name (pg_trgm, 0.6 or more) or a link (the related
  documents rule, ``document_links``; it counts as a shared identifier only for the same
  type), never a pair already suggested or answered either way. MAX_CANDIDATES are scored
  (``versions.outcome``); the best wins (auto over suggested, then text, then name, then the
  newest current version). Uploaded earlier: this is its new version. Uploaded later, still
  alone and never suggested (two of one batch, the later read first): that one is the new
  version of this family.
* **Accept** (V6, V9) locks both families in id order and, in one transaction, gives the
  document the family, the next number, the family's folder, tags, owner and label, and makes
  it current (the highest ready version); a family in no folder moves into the document's
  folder instead (issue 414). **Undo** (V7) puts it back alone, in the folder it
  had before (``previous_folder_id``), and the family's highest ready version is current again.
* A suggestion whose document or candidate family went to Trash or was deleted is no longer
  shown or answered (``visibility.LIVE_SUGGESTION``).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz import versions
from kaagaz.db import ai_settings, audit
from kaagaz.db.trash import lock_family, lock_live_family
from kaagaz.db.visibility import LIVE_SUGGESTION

FEATURE = "version_spotting"
MAX_CANDIDATES = 20


class Refused(Exception):
    """The change can't be made: ``code`` is not_found (404), or already_decided, not_ready,
    not_linked or not_newest (409)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- V1: the text and the fingerprint ---------------------------------------------------
_TEXT = text(
    "SELECT (e.span -> 'region' ->> 'page')::int AS page_no, e.span ->> 'text' AS text"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
    " AND NOT (coalesce(e.span ->> 'origin', '') = 'ocr' AND e.span ->> 'kind' IS NULL"
    " AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
    " ORDER BY 1, e.ord"
)
_SET_FINGERPRINT = text(
    "UPDATE documents SET name_key = :key, text_minhash = CAST(:minhash AS bigint[]),"
    " text_bands = CAST(:bands AS bigint[]) WHERE customer_id = :c AND id = :d"
)


def page_texts(conn: Connection, customer_id: str, document_id: str) -> dict[int, str]:
    """The first reader's text of each page, its spans in stored order, one per line.
    O(spans of the document)."""
    pages: dict[int, list[str]] = {}
    for page_no, span in conn.execute(_TEXT, {"c": customer_id, "d": document_id}):
        pages.setdefault(int(page_no), []).append(span or "")
    return {page_no: "\n".join(lines) for page_no, lines in pages.items()}


def write_fingerprint(conn: Connection, customer_id: str, document_id: str, display_name: str) -> None:
    """V1 and V2 for one document: its name key and fingerprint (empty under MIN_WORDS
    words). The caller holds the family lock."""
    pages = page_texts(conn, customer_id, document_id)
    found = versions.fingerprint("\n".join(pages[n] for n in sorted(pages)))
    minhash, bands = found or ([], [])
    params = {"c": customer_id, "d": document_id, "minhash": minhash, "bands": bands}
    conn.execute(_SET_FINGERPRINT, {**params, "key": versions.name_key(display_name)})


# --- V3 to V5: spotting ----------------------------------------------------------------
_ME = text(
    "SELECT d.display_name, d.status, d.deleted_at IS NOT NULL AS in_trash,"
    " (SELECT count(*) FROM documents o WHERE o.customer_id = d.customer_id AND o.family_id = d.family_id)"
    " AS versions,"
    " (SELECT count(*) FROM processing_runs r WHERE r.customer_id = d.customer_id AND r.document_id = d.id"
    " AND r.status = 'ready') AS ready_runs"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
)
# The candidates (V3): every arm of the OR names one of the two indexes or the links' key.
_CANDIDATES = text(
    "SELECT d.id, d.family_id, d.created_at > me.created_at AS later, d.text_minhash,"
    " d.doc_type IS NOT DISTINCT FROM me.doc_type AND d.doc_type IS NOT NULL AS same_type,"
    " d.folder_id IS NOT DISTINCT FROM me.folder_id AS same_folder,"
    " similarity(d.name_key, me.name_key) AS name_similarity,"
    " (SELECT min(l.identifier_kind) FROM document_links l WHERE l.customer_id = :c AND l.document_id = me.id"
    " AND l.linked_document_id = d.id AND d.doc_type = me.doc_type) AS shared_identifier,"
    " d.created_at FROM documents me JOIN documents d ON d.customer_id = me.customer_id"
    " WHERE me.customer_id = :c AND me.id = :d AND d.family_id <> me.family_id"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"  # LIVE_CURRENT
    " AND (d.text_bands && me.text_bands"
    " OR (d.name_key % me.name_key AND similarity(d.name_key, me.name_key) >= :name)"
    " OR EXISTS (SELECT 1 FROM document_links l WHERE l.customer_id = :c AND l.document_id = me.id"
    " AND l.linked_document_id = d.id))"
    " AND NOT EXISTS (SELECT 1 FROM version_suggestions s WHERE s.customer_id = :c"
    " AND ((s.document_id = me.id AND s.candidate_family_id = d.family_id)"
    " OR (s.document_id = d.id AND s.candidate_family_id = me.family_id)))"
    " AND (d.created_at < me.created_at OR (d.id = d.family_id"
    " AND NOT EXISTS (SELECT 1 FROM documents o WHERE o.customer_id = :c AND o.family_id = d.family_id"
    " AND o.id <> d.id)"
    " AND NOT EXISTS (SELECT 1 FROM version_suggestions s WHERE s.customer_id = :c"
    " AND s.document_id = d.id)))"
    " ORDER BY (d.text_bands && me.text_bands) DESC, name_similarity DESC NULLS LAST, d.created_at DESC"
    " LIMIT :n"
)
_MY_MINHASH = text("SELECT text_minhash, family_id FROM documents WHERE customer_id = :c AND id = :d")
_SUGGEST = text(
    "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, text_overlap,"
    " name_similarity, shared_identifier, outcome, status)"
    " VALUES (:c, :new, :family, :t, :n, :i, :outcome, 'pending')"
    " ON CONFLICT (document_id, candidate_family_id) DO NOTHING RETURNING id"
)
_RANK = {versions.AUTO: 2, versions.SUGGESTED: 1}


def spot(conn: Connection, customer_id: str, document_id: str) -> str | None:
    """V1 to V5 for a document just made ready: its fingerprint and name key always; then,
    on its first ready only (module notes), the best candidate's suggestion, audited as
    ``version.suggested`` (no actor), and linked at once when it is ``auto``. Returns a code
    for the log (off, none, suggested, auto) or None when there was nothing to spot. The
    family lock comes first. O(D) for this customer's D documents at worst (RLS may keep the
    GIN indexes out of the plan), plus MAX_CANDIDATES scored."""
    if lock_family(conn, customer_id, document_id) is None:
        return None
    params = {"c": customer_id, "d": document_id}
    me = conn.execute(_ME, params).mappings().one()
    if me["status"] != "ready":
        return None
    write_fingerprint(conn, customer_id, document_id, str(me["display_name"]))
    if me["in_trash"] or me["versions"] != 1 or me["ready_runs"] > 1:
        return None  # in Trash, already in a family, or a read again
    if not ai_settings.switch_on(conn, FEATURE, customer_id):
        return "off"
    minhash, family_id = conn.execute(_MY_MINHASH, params).one()
    candidates = conn.execute(
        _CANDIDATES, {**params, "name": versions.NAME_SIGNAL, "n": MAX_CANDIDATES}
    ).mappings()
    best: tuple[tuple[Any, ...], RowMapping, str, float | None] | None = None
    for row in candidates:
        overlap = versions.overlap(minhash, row["text_minhash"])
        name = None if row["name_similarity"] is None else float(row["name_similarity"])
        found = versions.outcome(
            overlap,
            name,
            row["shared_identifier"] is not None,
            same_type=bool(row["same_type"]),
            same_folder=bool(row["same_folder"]),
        )
        if found is None:
            continue
        key = (
            _RANK[found],
            overlap or 0.0,
            name or 0.0,
            row["shared_identifier"] is not None,
            row["created_at"],
        )
        if best is None or key > best[0]:
            best = (key, row, found, overlap)
    if best is None:
        return "none"
    _, row, found, overlap = best
    new, family = (str(row["id"]), str(family_id)) if row["later"] else (document_id, str(row["family_id"]))
    suggestion = conn.execute(
        _SUGGEST,
        {
            "c": customer_id,
            "new": new,
            "family": family,
            "t": overlap,
            "n": row["name_similarity"],
            "i": row["shared_identifier"],
            "outcome": found,
        },
    ).scalar()
    if suggestion is None:
        return "none"
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="version.suggested",
        target_type="document",
        target_id=new,
        details={"suggestion_id": str(suggestion), "candidate_family": family, "outcome": found},
    )
    if found == versions.AUTO:
        link(conn, customer_id, str(suggestion), by=None)
    return found


# --- V6, V9: accept and reject ----------------------------------------------------------
_SUGGESTION = text(
    "SELECT s.id, s.document_id, s.candidate_family_id, s.status, s.outcome, s.text_overlap,"
    " s.name_similarity, s.shared_identifier FROM version_suggestions s"
    " WHERE s.customer_id = :c AND s.id = :s"
)
_LINK_STATE = text(
    "SELECT n.status, n.deleted_at IS NOT NULL AS in_trash, n.folder_id AS from_folder,"
    " n.family_id = n.id AND NOT EXISTS (SELECT 1 FROM documents o WHERE o.customer_id = n.customer_id"
    " AND o.family_id = n.family_id AND o.id <> n.id) AS alone,"
    " (SELECT cur.id FROM documents cur WHERE cur.customer_id = n.customer_id"
    " AND cur.family_id = :f AND cur.is_current AND cur.deleted_at IS NULL) AS current_id,"
    " (SELECT cur.folder_id FROM documents cur WHERE cur.customer_id = n.customer_id"
    " AND cur.family_id = :f AND cur.is_current) AS folder_id,"
    " (SELECT max(v.version_no) FROM documents v WHERE v.customer_id = n.customer_id"
    " AND v.family_id = :f) + 1 AS version_no"
    " FROM documents n WHERE n.customer_id = :c AND n.id = :n"
)
_FAMILY_NOT_CURRENT = text(
    "UPDATE documents SET is_current = false WHERE customer_id = :c AND family_id = :f AND is_current"
)
# The highest ready version is current (the existing rule): the joining document is ready
# and gets the next number.
_JOIN = text(
    "UPDATE documents n SET family_id = :f, version_no = :v, folder_id = cur.folder_id,"
    " owner_id = cur.owner_id, sensitivity = cur.sensitivity,"
    " sensitivity_set_by_person = cur.sensitivity_set_by_person, autofile = NULL, is_current = true"
    " FROM documents cur WHERE n.customer_id = :c AND n.id = :n AND cur.customer_id = :c AND cur.id = :cur"
)
# Issue 414: a family in no folder joins the folder of the document linked to it, so the
# folder auto-filing made for that document is not left empty; both end in one folder.
_FAMILY_INTO = text(
    "UPDATE documents SET folder_id = :folder, autofile = NULL WHERE customer_id = :c AND family_id = :f"
)
_DROP_TAGS = text("DELETE FROM document_tags WHERE customer_id = :c AND document_id = :n")
_FAMILY_TAGS = text(
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT customer_id, :n, tag_id FROM document_tags WHERE customer_id = :c AND document_id = :cur"
)
_DECIDE = text(
    "UPDATE version_suggestions SET status = :status, decided_by = :by, decided_at = now()"
    " WHERE customer_id = :c AND id = :s AND status = :was RETURNING id"
)
# Accepted, with the folder the document leaves (Undo's way back, V7).
_ACCEPT = text(
    "UPDATE version_suggestions SET status = 'accepted', decided_by = :by, decided_at = now(),"
    " previous_folder_id = :previous WHERE customer_id = :c AND id = :s AND status = 'pending' RETURNING id"
)


def link(conn: Connection, customer_id: str, suggestion_id: str, *, by: str | None) -> str:
    """Make the suggestion's document the next version of its candidate family (V6): both
    families locked in id order, then in this transaction the family, number, folder, tags,
    owner and label, current; the suggestion accepted by ``by`` (None: Kaagaz, ``auto``), and
    one ``version.accepted`` row naming the folders. The accept route writes the note (V9) and
    the details stamp in the same transaction. Returns the document's id. Raises Refused:
    not_found (no such live suggestion), already_decided, or not_ready. O(v log D)."""
    params: dict[str, Any] = {"c": customer_id, "s": suggestion_id}
    first = conn.execute(_SUGGESTION, params).mappings().first()
    if first is None:
        raise Refused("not_found")
    new, family = str(first["document_id"]), str(first["candidate_family_id"])
    for document in sorted((new, family)):
        if lock_family(conn, customer_id, document) is None:
            raise Refused("not_found")  # deleted for good
    suggestion = conn.execute(_SUGGESTION, params).mappings().one()  # again, under the locks
    state = conn.execute(_LINK_STATE, {**params, "n": new, "f": family}).mappings().one()
    if state["in_trash"] or state["current_id"] is None or not state["alone"]:
        raise Refused("not_found")  # the suggestion disappeared (section 10)
    if suggestion["status"] != "pending":
        raise Refused("already_decided")
    if state["status"] != "ready":
        raise Refused("not_ready")
    # First, and only while still pending: a reject (no family locks) racing this one wins or loses
    # here, before anything else is written.
    if conn.execute(_ACCEPT, {**params, "by": by, "previous": state["from_folder"]}).first() is None:
        raise Refused("already_decided")
    ids = {**params, "n": new, "f": family, "cur": str(state["current_id"]), "v": int(state["version_no"])}
    folder_id = state["folder_id"]
    if folder_id is None and state["from_folder"] is not None:
        folder_id = state["from_folder"]
        conn.execute(_FAMILY_INTO, {**ids, "folder": folder_id})
    conn.execute(_FAMILY_NOT_CURRENT, ids)
    conn.execute(_JOIN, ids)
    conn.execute(_DROP_TAGS, ids)
    conn.execute(_FAMILY_TAGS, ids)
    folder = None if folder_id is None else str(folder_id)
    audit.record(
        conn,
        customer_id,
        actor_user_id=by,
        action="version.accepted",
        target_type="document",
        target_id=new,
        details={
            "suggestion_id": suggestion_id,
            "family_id": family,
            "version_no": ids["v"],
            "outcome": str(suggestion["outcome"]),
            "from_folder_id": None if state["from_folder"] is None else str(state["from_folder"]),
            "folder_id": folder,
        },
    )
    return new


_LIVE = text(
    "SELECT s.id, s.document_id, s.candidate_family_id, s.status FROM version_suggestions s"
    " WHERE s.customer_id = :c AND s.id = :s"
    " AND EXISTS (SELECT 1 FROM documents sn WHERE sn.customer_id = s.customer_id AND sn.id = s.document_id"
    " AND sn.deleted_at IS NULL)"
    " AND EXISTS (SELECT 1 FROM documents sc WHERE sc.customer_id = s.customer_id"
    " AND sc.family_id = s.candidate_family_id AND sc.deleted_at IS NULL)"
)


def reject(conn: Connection, customer_id: str, suggestion_id: str, *, by: str) -> str:
    """Keep separate (V6): the suggestion rejected by ``by``, so the pair is never suggested
    again (the unique pair, which spotting never repeats), and one ``version.rejected`` row.
    Returns the document's id. Raises Refused: not_found or already_decided. 2 statements."""
    params = {"c": customer_id, "s": suggestion_id}
    row = conn.execute(_LIVE, params).mappings().first()
    if row is None:
        raise Refused("not_found")
    if conn.execute(_DECIDE, {**params, "status": "rejected", "by": by, "was": "pending"}).first() is None:
        raise Refused("already_decided")
    audit.record(
        conn,
        customer_id,
        actor_user_id=by,
        action="version.rejected",
        target_type="document",
        target_id=str(row["document_id"]),
        details={"suggestion_id": suggestion_id, "candidate_family": str(row["candidate_family_id"])},
    )
    return str(row["document_id"])


# --- V7: Undo ---------------------------------------------------------------------------
_UNDO_STATE = text(
    "SELECT d.version_no, (SELECT max(v.version_no) FROM documents v WHERE v.customer_id = :c"
    " AND v.family_id = :f) AS newest,"
    " (SELECT s.id FROM version_suggestions s WHERE s.customer_id = :c AND s.document_id = :d"
    " AND s.candidate_family_id = :f AND s.status = 'accepted') AS suggestion_id,"
    " (SELECT fo.id FROM version_suggestions s JOIN folders fo ON fo.customer_id = s.customer_id"
    " AND fo.id = s.previous_folder_id WHERE s.customer_id = :c AND s.document_id = :d"
    " AND s.candidate_family_id = :f AND s.status = 'accepted') AS folder_id"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
)
_ALONE_AGAIN = text(
    "UPDATE documents SET family_id = id, version_no = 1, folder_id = :folder,"
    " is_current = (status = 'ready') WHERE customer_id = :c AND id = :d"
)
_HIGHEST_READY_CURRENT = text(
    "UPDATE documents SET is_current = true WHERE customer_id = :c AND id = (SELECT v.id FROM documents v"
    " WHERE v.customer_id = :c AND v.family_id = :f AND v.status = 'ready'"
    " ORDER BY v.version_no DESC LIMIT 1)"
    # A version a person made current since the link stays current.
    " AND NOT EXISTS (SELECT 1 FROM documents x WHERE x.customer_id = :c AND x.family_id = :f"
    " AND x.is_current)"
)


def undo(conn: Connection, customer_id: str, document_id: str, *, by: str) -> None:
    """V7: a document linked from a suggestion (accepted or auto) gets its own family back,
    as version 1, in the folder it had before (the top level when that folder is gone); the
    family's highest ready version is current again; the suggestion is ``undone`` and one
    ``version.undone`` row is written. Raises Refused: not_found, not_linked, or not_newest
    once a later version exists; trash.InTrash in Trash. O(v log D).
    ponytail: "the previous current" is the highest ready one left (one made current by hand
    since the link stays); a version made current by hand before the link is not remembered."""
    family = lock_live_family(conn, customer_id, document_id)
    if family is None:
        raise Refused("not_found")
    params = {"c": customer_id, "d": document_id, "f": family}
    state = conn.execute(_UNDO_STATE, params).mappings().one()
    if state["suggestion_id"] is None:
        raise Refused("not_linked")
    if state["version_no"] < state["newest"]:
        raise Refused("not_newest")
    folder = None if state["folder_id"] is None else str(state["folder_id"])
    suggestion = str(state["suggestion_id"])
    # Before the split, so the row carries the family and shows on its timeline (A5).
    audit.record(
        conn,
        customer_id,
        actor_user_id=by,
        action="version.undone",
        target_type="document",
        target_id=document_id,
        details={"suggestion_id": suggestion, "family_id": family, "folder_id": folder},
    )
    conn.execute(_ALONE_AGAIN, {**params, "folder": folder})
    conn.execute(_HIGHEST_READY_CURRENT, params)
    conn.execute(
        _DECIDE, {"c": customer_id, "s": suggestion, "status": "undone", "by": by, "was": "accepted"}
    )


# --- V10: make current ------------------------------------------------------------------
_STATE = text("SELECT status, is_current, version_no FROM documents WHERE customer_id = :c AND id = :d")
# The others first, then this one: the unique index documents_current is checked row by row.
_OTHERS_NOT_CURRENT = text(
    "UPDATE documents SET is_current = false WHERE customer_id = :c AND family_id = :f AND id <> :d"
    " AND is_current"
)
_THIS_CURRENT = text("UPDATE documents SET is_current = true WHERE customer_id = :c AND id = :d")


def make_current(conn: Connection, customer_id: str, document_id: str, *, by: str) -> None:
    """V10: this ready version, out of Trash, is its family's one current version, in one
    transaction under the family lock, audited as ``version.make_current``. Raises Refused:
    not_found or not_ready; trash.InTrash in Trash. O(v log D)."""
    family = lock_live_family(conn, customer_id, document_id)
    if family is None:
        raise Refused("not_found")
    params = {"c": customer_id, "d": document_id, "f": family}
    state = conn.execute(_STATE, params).mappings().one()
    if state["status"] != "ready":
        raise Refused("not_ready")
    conn.execute(_OTHERS_NOT_CURRENT, params)
    conn.execute(_THIS_CURRENT, params)
    audit.record(
        conn,
        customer_id,
        actor_user_id=by,
        action="version.make_current",
        target_type="document",
        target_id=document_id,
        details={"family_id": family, "version_no": int(state["version_no"])},
    )


# --- what the screens read (V5, V6, V11) ------------------------------------------------
# The candidate side's document: its current version, else (an auto link) the newest other.
_CANDIDATE = (
    " JOIN LATERAL (SELECT o.id, o.display_name FROM documents o WHERE o.customer_id = s.customer_id"
    " AND o.family_id = s.candidate_family_id AND o.id <> s.document_id AND o.deleted_at IS NULL"
    " ORDER BY o.is_current DESC, o.version_no DESC LIMIT 1) cand ON true"
)
_VIEW = (
    "SELECT s.id, s.document_id, n.display_name AS document_name, s.candidate_family_id,"  # noqa: S608 - fixed fragments only
    " cand.id AS candidate_id, cand.display_name AS candidate_name, s.text_overlap, s.name_similarity,"
    " s.shared_identifier, s.outcome, s.status, s.created_at,"
    " (SELECT max(v.version_no) FROM documents v WHERE v.customer_id = s.customer_id"
    " AND v.family_id = s.candidate_family_id) + 1 AS next_version_no"
    " FROM version_suggestions s"
    f" JOIN documents n ON n.customer_id = s.customer_id AND n.id = s.document_id{_CANDIDATE}"
)
_FOR_DOCUMENT = text(
    f"{_VIEW} WHERE s.customer_id = :c AND {LIVE_SUGGESTION}"
    " AND (s.document_id = :d OR s.candidate_family_id = :f)"
    " ORDER BY (s.document_id = :d) DESC, s.created_at DESC LIMIT 1"
)
_IN_BATCH = text(
    f"{_VIEW} WHERE s.customer_id = :c AND n.batch_id = :b AND n.uploaded_by = :u AND n.deleted_at IS NULL"
    f" AND (({LIVE_SUGGESTION}) OR (s.outcome = 'auto' AND s.status = 'accepted'))"
    " ORDER BY s.created_at, s.id LIMIT :limit"
)
_ALLOWED = text(
    "SELECT a.family_id = b.family_id OR EXISTS (SELECT 1 FROM version_suggestions s"  # noqa: S608 - fixed fragments only
    f" WHERE s.customer_id = :c AND {LIVE_SUGGESTION}"
    " AND ((s.document_id = a.id AND s.candidate_family_id = b.family_id)"
    " OR (s.document_id = b.id AND s.candidate_family_id = a.family_id)))"
    " FROM documents a JOIN documents b ON b.customer_id = a.customer_id AND b.id = :b"
    " WHERE a.customer_id = :c AND a.id = :a"
)


def view(row: Mapping[Any, Any], document_id: str) -> dict[str, Any]:
    """``VersionSuggestion``: the suggestion, its two sides, the next number, the scores and
    the banner's reasons; ``role`` new (this document is the new version) or candidate."""
    text_overlap = None if row["text_overlap"] is None else float(row["text_overlap"])
    name = None if row["name_similarity"] is None else float(row["name_similarity"])
    return {
        "id": str(row["id"]),
        "role": "new" if str(row["document_id"]) == document_id else "candidate",
        "outcome": row["outcome"],
        "status": row["status"],
        "document": {"id": str(row["document_id"]), "name": row["document_name"]},
        "candidate": {
            "family_id": str(row["candidate_family_id"]),
            "id": str(row["candidate_id"]),
            "name": row["candidate_name"],
        },
        "next_version_no": None if row["next_version_no"] is None else int(row["next_version_no"]),
        "text_overlap": text_overlap,
        "name_similarity": name,
        "shared_identifier": row["shared_identifier"],
        "reasons": versions.reasons(text_overlap, name, row["shared_identifier"]),
    }


def for_document(
    conn: Connection, customer_id: str, document_id: str, family_id: str
) -> dict[str, Any] | None:
    """The live pending suggestion naming this document as the new version, else its family
    as the candidate; None when there is none. One statement."""
    params = {"c": customer_id, "d": document_id, "f": family_id}
    row = conn.execute(_FOR_DOCUMENT, params).mappings().first()
    return None if row is None else view(row, document_id)


def in_batch(
    conn: Connection, customer_id: str, batch_id: str, *, user_id: str, limit: int
) -> list[dict[str, Any]]:
    """V5's tray: the batch's live pending suggestions and its automatic links, oldest first,
    at most ``limit``. One statement on the documents_batch index."""
    params = {"c": customer_id, "b": batch_id, "u": user_id, "limit": limit}
    return [view(row, str(row["document_id"])) for row in conn.execute(_IN_BATCH, params).mappings()]


def may_compare(conn: Connection, customer_id: str, first: str, second: str) -> bool | None:
    """V11: the two are in one family or are a live pending suggestion's pair; None when
    either is not this customer's. One statement."""
    allowed = conn.execute(_ALLOWED, {"c": customer_id, "a": first, "b": second}).scalar()
    return None if allowed is None else bool(allowed)


# --- the one-time sweep (section 7) ------------------------------------------------------
_MISSING = text(
    "SELECT id FROM documents WHERE customer_id = :c AND status = 'ready' AND text_minhash IS NULL"
    " ORDER BY id LIMIT :n"
)
_NAME_OF = text("SELECT display_name, status FROM documents WHERE customer_id = :c AND id = :d")


def missing(conn: Connection, customer_id: str, limit: int) -> Sequence[str]:
    """This customer's ready documents never fingerprinted, at most ``limit``."""
    return [str(row[0]) for row in conn.execute(_MISSING, {"c": customer_id, "n": limit})]


def fill(conn: Connection, customer_id: str, document_id: str) -> bool:
    """The sweep's one document: its fingerprint and name key, under the family lock, with no
    spotting (old documents are never suggested). False when it is gone or no longer ready."""
    if lock_family(conn, customer_id, document_id) is None:
        return False
    row = conn.execute(_NAME_OF, {"c": customer_id, "d": document_id}).first()
    if row is None or row[1] != "ready":
        return False
    write_fingerprint(conn, customer_id, document_id, str(row[0]))
    return True
