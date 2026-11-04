"""What the document screens read and change: the file list and its filters,
one document, its pages, values and spreadsheet cells, rename and move.

Why here: the API only shapes JSON; the SQL lives in one reviewable place.
Every function takes a connection from ``Database.customer(customer_id)`` and
filters on ``customer_id`` as well (two locks, docs/architecture.md).

The ``q`` search matches ``display_name`` with ILIKE. The user's text is a bound
parameter, and ``%``, ``_`` and ``\\`` in it are escaped, so it is matched
literally.

The list's newer filters (PRD 1 F4) are fixed SQL fragments, and Python adds
only the ones that are set, so the statement never carries a catch-all such as
"(:x IS NULL OR ...)" for them. The fragments are constants and every value is
a bound parameter, never text put into the SQL.

A folder and tags belong to the whole family (plan 9.3), so a move files every
version; a name belongs to one version, so a rename changes only that one.

What the list shows (PRD 1 F5 and F6, plan 9.7): the current version of each
family, out of Trash (``LISTED``), or only Trash (``TRASHED_LISTED``). The
current version is the one search and chat use, so a newer version that is
still being read, or failed, never hides it; that newer one comes along in the
row's ``newer`` column instead. The fragment is written literally in ``_LIST``
and ``_TRASH_LIST``; a guard test checks it. Lookups by id (``_SELECT``,
``_VERSIONS``) have no such filter, so a document in Trash or an older version
still opens.

Every row also carries ``family_uploaders``: who uploaded each version of its
family. Moving a family to Trash or back acts on every version, so only an
admin, or a user who uploaded every version, may do it (``can_delete``).
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.chunking.context import document_part
from kaagaz.db.documents import STUCK, folder_missing
from kaagaz.db.folders import FolderNotFound, prune_empty
from kaagaz.db.review import WAITING_VALUE
from kaagaz.db.visibility import LIVE_SUGGESTION
from kaagaz.versions import name_key

# File management (Q3, Q4, F5, O1 to O3): the owner and label, the version count, the
# document date, the last change (the newest upload or the details stamp, whichever is
# newer) and who made it, the department (the top-level folder above the document, when it
# is a department; at most 100 levels up) and a pending version suggestion naming this
# document as the new one (``new``) or its family as the candidate (``candidate``).
# ponytail: the candidate probe has no index of its own; suggestions are few per customer.
_LAST_UPLOAD = (
    "(SELECT lu.created_at FROM documents lu WHERE lu.customer_id = d.customer_id"
    " AND lu.family_id = d.family_id ORDER BY lu.created_at DESC LIMIT 1)"
)
_LAST_UPLOADER = (
    "(SELECT lu.uploaded_by FROM documents lu WHERE lu.customer_id = d.customer_id"
    " AND lu.family_id = d.family_id ORDER BY lu.created_at DESC LIMIT 1)"
)
_FILE_COLUMNS = (
    " d.owner_id, d.sensitivity, d.document_date, d.version_note,"  # noqa: S608 - fixed fragments only
    " (SELECT ow.display_name FROM users ow WHERE ow.customer_id = d.customer_id AND ow.id = d.owner_id)"
    " AS owner_name,"
    " (SELECT count(*) FROM documents vc WHERE vc.customer_id = d.customer_id AND vc.family_id = d.family_id)"
    " AS versions,"
    f" GREATEST(d.details_updated_at, {_LAST_UPLOAD}) AS last_updated_at,"
    " (SELECT ub.display_name FROM users ub WHERE ub.customer_id = d.customer_id AND ub.id ="
    f" CASE WHEN d.details_updated_at >= {_LAST_UPLOAD} THEN d.details_updated_by ELSE {_LAST_UPLOADER} END)"
    " AS last_updated_by_name,"
    " (WITH RECURSIVE up (id, parent_id, depth) AS ("
    " SELECT f.id, f.parent_id, 1 FROM folders f WHERE f.customer_id = d.customer_id AND f.id = d.folder_id"
    " UNION ALL SELECT f.id, f.parent_id, up.depth + 1 FROM up JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = d.customer_id AND up.depth < 101)"
    " SELECT json_build_object('id', dep.id, 'name', dep.name) FROM up"
    " JOIN folders dep ON dep.customer_id = d.customer_id AND dep.id = up.id"
    " WHERE up.parent_id IS NULL AND dep.kind = 'department') AS department,"
    " (SELECT json_build_object('id', s.id,"
    " 'role', CASE WHEN s.document_id = d.id THEN 'new' ELSE 'candidate' END)"
    f" FROM version_suggestions s WHERE s.customer_id = d.customer_id AND {LIVE_SUGGESTION}"
    " AND (s.document_id = d.id OR s.candidate_family_id = d.family_id)"
    " ORDER BY (s.document_id = d.id) DESC, s.created_at DESC LIMIT 1) AS pending_suggestion"
)

# A DocumentSummary row: the document, its uploader's name, who uploaded each
# version of its family, and the newest version after it that is not in Trash
# (one small JSON object, or NULL). Both subqueries use the index
# documents_family_version on the family's few rows. PRD 5 F4: the pages read
# unreadable or uncertain that are still on the attention list (not kept as is,
# F5), and the values still waiting to be verified, each counted on the document's own
# rows (the pages key, the index extracted_values_record). PRD 5 U2: the unsure and
# illegible words on those pages. PRD 5 Q6: ``batch_ahead``, the files of the same uploader's batch (the
# documents_batch index) uploaded before this one and still being read or waiting; 0
# for a document in no batch. It counts this customer's own rows only, so another
# customer's load never shows in it.
_COLUMNS = (
    "SELECT d.id, d.display_name, d.detected_type, d.size_bytes, d.status, d.failure_code,"  # noqa: S608 - fixed fragments, review.WAITING_VALUE and documents.STUCK
    " d.page_count, d.created_at, d.uploaded_by, d.folder_id, d.deleted_at, d.family_id, d.version_no,"
    " d.attempts, d.max_attempts, d.progress, d.doc_type, d.languages, d.summary, d.read_model,"
    " u.display_name AS uploaded_by_name,"
    " (SELECT count(*) FROM pages pu WHERE pu.customer_id = d.customer_id AND pu.document_id = d.id"
    " AND pu.read_quality = 'unreadable' AND pu.accepted_at IS NULL) AS pages_unreadable,"
    " (SELECT count(*) FROM pages pc WHERE pc.customer_id = d.customer_id AND pc.document_id = d.id"
    " AND pc.read_quality = 'uncertain' AND pc.accepted_at IS NULL) AS pages_uncertain,"
    " (SELECT coalesce(sum(pw.unsure_words + pw.illegible_words), 0) FROM pages pw"
    " WHERE pw.customer_id = d.customer_id AND pw.document_id = d.id AND pw.accepted_at IS NULL)"
    " AS words_unsure,"
    " (SELECT count(*) FROM documents b WHERE b.customer_id = d.customer_id AND b.batch_id = d.batch_id"
    " AND b.uploaded_by = d.uploaded_by AND b.deleted_at IS NULL AND b.status IN ('received', 'clean')"
    " AND (b.created_at, b.id) < (d.created_at, d.id)) AS batch_ahead,"
    # Every value still waiting to be verified (review.WAITING_VALUE, the review queue's own
    # definition, QA #366), so "All values verified" is never said while one waits.
    " (SELECT count(*) FROM extracted_values v WHERE v.customer_id = d.customer_id"
    f" AND v.document_id = d.id AND {WAITING_VALUE}) AS values_to_review,"
    " (SELECT array_agg(o.uploaded_by) FROM documents o"
    " WHERE o.customer_id = d.customer_id AND o.family_id = d.family_id) AS family_uploaders,"
    # Issue 407: in progress RETRY_STUCK_HOURS since it was asked for (documents.STUCK), so it
    # may be read again; and while it waits for the reader ({"step": "waiting"}), its last
    # run's code, which says what it waits on (on the index processing_runs_document).
    f" {STUCK} AS stuck,"
    " CASE WHEN d.status IN ('received', 'clean') AND d.progress ->> 'step' = 'waiting'"
    " THEN (SELECT r.error_code FROM processing_runs r WHERE r.customer_id = d.customer_id"
    " AND r.document_id = d.id ORDER BY r.started_at DESC LIMIT 1) END AS waiting_code,"
    # Auto-filing (feature 28), from the family's first version: filed while it is still in a
    # folder; not filed when skipped, or read as current with no type (the filing that runs
    # right after a typed read is not shown as "not filed" in the moment before it commits).
    " (SELECT CASE WHEN r.autofile = 'filed' AND r.folder_id IS NOT NULL THEN 'filed'"
    " WHEN r.autofile = 'skipped' OR (r.autofile = 'pending' AND d.status = 'ready' AND d.is_current"
    " AND d.doc_type IS NULL) THEN 'not_filed' END"
    " FROM documents r WHERE r.customer_id = d.customer_id AND r.id = d.family_id) AS autofile,"
    " (SELECT json_build_object('id', n.id, 'version_no', n.version_no, 'status', n.status,"
    " 'failure_code', n.failure_code, 'detected_type', n.detected_type, 'page_count', n.page_count,"
    " 'attempts', n.attempts, 'max_attempts', n.max_attempts, 'progress', n.progress,"
    " 'waiting_code', CASE WHEN n.progress ->> 'step' = 'waiting' THEN (SELECT r.error_code"  # issue 407
    " FROM processing_runs r WHERE r.customer_id = n.customer_id AND r.document_id = n.id"
    " ORDER BY r.started_at DESC LIMIT 1) END)"
    " FROM documents n WHERE n.customer_id = d.customer_id AND n.family_id = d.family_id"
    " AND n.version_no > d.version_no AND n.deleted_at IS NULL"
    " ORDER BY n.version_no DESC LIMIT 1) AS newer," + _FILE_COLUMNS
)

_COUNT = "SELECT count(*)"

# By id: the detail, the file, the pages and the reply to an upload.
_SELECT = (
    _COLUMNS + " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.id = :d"
)

# The list's rows: the current version of each family (the unique index
# documents_current allows one per family), not in Trash. _COLUMNS or _COUNT goes first.
_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
)

# The same, in Trash (trashed=true).
_TRASH_LIST = (
    " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL AND d.is_current"
)

# V12: each version's note, uploader and how it joined: linked from a suggestion (by a person
# or on its own, ``auto``) or uploaded.
_VERSIONS = (
    "SELECT d.id, d.version_no, d.status, d.is_current, d.created_at, d.display_name, d.version_note,"
    " d.uploaded_by, u.display_name AS uploaded_by_name,"
    " (SELECT s.outcome FROM version_suggestions s WHERE s.customer_id = d.customer_id"
    " AND s.document_id = d.id AND s.candidate_family_id = d.family_id AND s.status = 'accepted') AS linked"
    " FROM documents d LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.family_id = :f ORDER BY d.version_no"
)

_FILTERS = (
    " AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text))"
    " AND (CAST(:pattern AS text) IS NULL OR d.display_name ILIKE CAST(:pattern AS text) ESCAPE '\\')"
)

_TOP_LEVEL = " AND d.folder_id IS NULL"
_IN_FOLDERS = " AND d.folder_id = ANY(CAST(:folders AS uuid[]))"
# Every tag must match: the documents holding all :tag_count distinct tags asked for.
_WITH_TAGS = (
    " AND d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:tags AS uuid[]))"
    " GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = :tag_count)"
)
_OF_TYPE = " AND d.detected_type = :detected_type"
# PRD 5 U2 "Needs attention": a page unreadable or uncertain still on the attention list
# (not kept as is), or a value to review; the same rows _COLUMNS counts.
_NEEDS_ATTENTION = (
    " AND (EXISTS (SELECT 1 FROM pages pa WHERE pa.customer_id = :c AND pa.document_id = d.id"  # noqa: S608 - a fixed fragment, review.WAITING_VALUE
    " AND pa.read_quality IN ('unreadable', 'uncertain') AND pa.accepted_at IS NULL)"
    " OR EXISTS (SELECT 1 FROM extracted_values v WHERE v.customer_id = :c"
    f" AND v.document_id = d.id AND {WAITING_VALUE}))"
)
_UPLOADED_FROM = " AND d.created_at >= :uploaded_from"
_UPLOADED_TO = " AND d.created_at < :uploaded_to"
# File management Q3: the department (its whole subtree), owner, labels, a party on the card
# (its name as written or in Latin letters, contained, any letter case), a survey number on
# the card (as autofile.survey_digits reads it), the document date and a newer version
# waiting to be confirmed.
_IN_DEPARTMENT = " AND d.folder_id = ANY(CAST(:department_folders AS uuid[]))"
# The dashboard's "Not in a department": in no folder, or in one under no department (its
# _DASHBOARD_DEPARTMENTS tree, so the list holds exactly the files it counts).
_OUTSIDE_DEPARTMENTS = (
    " AND (d.folder_id IS NULL OR d.folder_id NOT IN (WITH RECURSIVE tree (id, depth) AS ("
    " SELECT id, 1 FROM folders WHERE customer_id = :c AND parent_id IS NULL AND kind = 'department'"
    " UNION ALL SELECT f.id, t.depth + 1 FROM tree t JOIN folders f ON f.parent_id = t.id"
    " WHERE f.customer_id = :c AND t.depth < 101) SELECT id FROM tree))"
)
_OF_OWNER = " AND d.owner_id = :owner_id"
_LABELLED = " AND d.sensitivity = ANY(CAST(:sensitivities AS text[]))"
_WITH_PARTY = (
    " AND EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(d.card -> 'parties', '[]'::jsonb)) p"
    " WHERE p ->> 'name' ILIKE :party ESCAPE '\\' OR p ->> 'name_latin' ILIKE :party ESCAPE '\\')"
)
_WITH_PROPERTY = (
    " AND EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(d.card -> 'identifiers', '[]'::jsonb)) i"
    " WHERE i ->> 'kind' = 'survey_number'"
    " AND substring(translate(regexp_replace(i ->> 'value', '\\s', '', 'g'),"
    " '०१२३४५६७८९', '0123456789') from '[0-9]+(?:/[0-9]+)*') = :property)"
)
_DATED_FROM = " AND d.document_date >= :doc_date_from"
_DATED_TO = " AND d.document_date <= :doc_date_to"
_HAS_NEWER = (
    " AND EXISTS (SELECT 1 FROM version_suggestions s WHERE s.customer_id = :c"  # noqa: S608 - visibility.LIVE_SUGGESTION
    f" AND s.candidate_family_id = d.family_id AND {LIVE_SUGGESTION})"
)
# Q4: the sort keys; the label by its strength. Output-column names (owner_name,
# last_updated_at) are allowed alone in ORDER BY.
SORTS = {
    "name": "lower(d.display_name)",
    "doc_type": "d.doc_type",
    "owner": "owner_name",
    "sensitivity": "array_position(ARRAY['public', 'internal', 'confidential', 'restricted'], d.sensitivity)",
    "version": "d.version_no",
    "document_date": "d.document_date",
    "updated_at": "last_updated_at",
    "created_at": "d.created_at",
}

_RENAME = "UPDATE documents SET display_name = :name, name_key = :key WHERE customer_id = :c AND id = :d"
_NAME_OF = "SELECT display_name FROM documents WHERE customer_id = :c AND id = :d"
# The context header's first part is "Document: {name}" (PRD 2 C4), followed by
# " | " or nothing; a rename swaps it for the new name in each of the document's pieces.
_RENAME_HEADERS = (
    "UPDATE pieces SET context_header = CASE"
    " WHEN context_header = :old THEN :new"
    " WHEN :new = '' THEN substr(context_header, length(:old) + 4)"
    " ELSE :new || substr(context_header, length(:old) + 1) END"
    " WHERE customer_id = :c AND document_id = :d AND :old <> ''"
    " AND (context_header = :old OR left(context_header, length(:old) + 3) = :old || ' | ')"
)

# Auto-filing (feature 28, R3): a person's move, to the top level too, ends the family's
# auto-filing state, so Kaagaz never moves it after them.
_MOVE_FAMILY = (
    "UPDATE documents SET folder_id = :f, autofile = NULL WHERE customer_id = :c"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
)
# The folders the family is in now, before a move.
_FAMILY_FOLDERS = (
    "SELECT DISTINCT folder_id FROM documents WHERE customer_id = :c AND folder_id IS NOT NULL"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
)


def _like_pattern(contains: str | None) -> str | None:
    if not contains:
        return None
    escaped = contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_documents(
    conn: Connection,
    customer_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None,
    name_contains: str | None,
    top_level_only: bool = False,
    folder_ids: Sequence[str] | None = None,
    tag_ids: Sequence[str] = (),
    detected_type: str | None = None,
    uploaded_from: datetime | None = None,
    uploaded_to: datetime | None = None,
    trashed: bool = False,
    attention: bool = False,
    department_folders: Sequence[str] | None = None,
    outside_departments: bool = False,
    owner_id: str | None = None,
    sensitivities: Sequence[str] = (),
    party: str | None = None,
    property_number: str | None = None,
    doc_date_from: date | None = None,
    doc_date_to: date | None = None,
    has_newer_version: bool = False,
    sort: str = "created_at",
    descending: bool = True,
) -> tuple[Sequence[RowMapping], int]:
    """One page of this customer's documents, newest first unless ``sort`` (Q4: a SORTS key)
    and ``descending`` say otherwise, and the total that match.

    One row per family: its current version, out of Trash, or with ``trashed``
    only what is in Trash.

    Filters (each left at its default filters nothing): ``top_level_only`` (in no
    folder), ``folder_ids`` (in one of these folders), ``tag_ids`` (holding every
    one of these tags), ``detected_type``, and ``uploaded_from`` (inclusive) to
    ``uploaded_to`` (exclusive), ``attention`` (PRD 5 U2: a page or a value needs
    attention); File management Q3: ``department_folders`` (a department's subtree),
    ``outside_departments`` (the dashboard's "Not in a department"),
    ``owner_id``, ``sensitivities`` (any of), ``party``, ``property_number`` (normalised),
    ``doc_date_from`` to ``doc_date_to`` (both inclusive) and ``has_newer_version``. All of
    them apply in both queries, before LIMIT. A sort other than ``created_at`` reads every
    match before the page.

    Two queries (the page and the count). Cost for this customer's D documents:
    no filter, O(limit + offset) rows on the (customer_id, created_at) index; a
    folder filter, O(n log n) for the n documents in those folders (index
    ``documents_folder``); a tag filter, O(L) for the L links of those tags
    (index ``document_tags_tag``), grouped, then one semi-join; ``q``, the type
    or the dates alone, O(D) rows looked at; ``attention`` adds two key probes a row.
    The count reads every match.
    Each row looked at costs one more O(log D) probe for a newer version.
    """
    params: dict[str, Any] = {
        "c": customer_id,
        "status": status,
        "pattern": _like_pattern(name_contains),
        "limit": limit,
        "offset": offset,
    }
    chosen: list[str] = []
    if top_level_only:
        chosen.append(_TOP_LEVEL)
    elif folder_ids is not None:
        chosen.append(_IN_FOLDERS)
        params["folders"] = list(folder_ids)
    wanted_tags = list(dict.fromkeys(tag_ids))
    if wanted_tags:
        chosen.append(_WITH_TAGS)
        params["tags"], params["tag_count"] = wanted_tags, len(wanted_tags)
    if detected_type is not None:
        chosen.append(_OF_TYPE)
        params["detected_type"] = detected_type
    if uploaded_from is not None:
        chosen.append(_UPLOADED_FROM)
        params["uploaded_from"] = uploaded_from
    if uploaded_to is not None:
        chosen.append(_UPLOADED_TO)
        params["uploaded_to"] = uploaded_to
    if attention:
        chosen.append(_NEEDS_ATTENTION)
    chosen.append(
        file_filters(
            params,
            department_folders=department_folders,
            outside_departments=outside_departments,
            owner_id=owner_id,
            sensitivities=sensitivities,
            party=party,
            property_number=property_number,
            doc_date_from=doc_date_from,
            doc_date_to=doc_date_to,
            has_newer_version=has_newer_version,
        )
    )
    where = (_TRASH_LIST if trashed else _LIST) + _FILTERS + "".join(chosen)
    way = "DESC" if descending else "ASC"
    nulls = "" if sort == "created_at" else " NULLS LAST"  # created_at: the listing index, as before
    order = f" ORDER BY {SORTS[sort]} {way}{nulls}, d.id {way}"
    rows = (
        conn.execute(
            text(_COLUMNS + where + order + " LIMIT :limit OFFSET :offset"),
            params,
        )
        .mappings()
        .all()
    )
    total: int = conn.execute(text(_COUNT + where), params).scalar_one()
    return rows, int(total)


def file_filters(
    params: dict[str, Any],
    *,
    department_folders: Sequence[str] | None = None,
    outside_departments: bool = False,
    owner_id: str | None = None,
    sensitivities: Sequence[str] = (),
    party: str | None = None,
    property_number: str | None = None,
    doc_date_from: date | None = None,
    doc_date_to: date | None = None,
    has_newer_version: bool = False,
) -> str:
    """Q3's fixed fragments for the filters given, their values added to ``params``; the list
    and search (``matching_ids``) share them. O(1)."""
    optional = (
        (
            department_folders is not None,
            _IN_DEPARTMENT,
            "department_folders",
            list(department_folders or ()),
        ),
        (outside_departments, _OUTSIDE_DEPARTMENTS, "outside_departments", None),
        (owner_id is not None, _OF_OWNER, "owner_id", owner_id),
        (bool(sensitivities), _LABELLED, "sensitivities", list(dict.fromkeys(sensitivities))),
        (bool(party), _WITH_PARTY, "party", _like_pattern(party)),
        (bool(property_number), _WITH_PROPERTY, "property", property_number),
        (doc_date_from is not None, _DATED_FROM, "doc_date_from", doc_date_from),
        (doc_date_to is not None, _DATED_TO, "doc_date_to", doc_date_to),
        (has_newer_version, _HAS_NEWER, "has_newer", None),
    )
    chosen = []
    for wanted, fragment, name, value in optional:
        if wanted:
            chosen.append(fragment)
            params[name] = value
    return "".join(chosen)


def matching_ids(conn: Connection, customer_id: str, **filters: Any) -> list[str]:
    """Q3 on search: the current, untrashed documents ``file_filters`` keeps. One query over
    this customer's current documents: O(D)."""
    params: dict[str, Any] = {"c": customer_id}
    where = _LIST + file_filters(params, **filters)
    found: Sequence[object] = conn.execute(text("SELECT d.id" + where), params).scalars().all()
    return [str(document) for document in found]


def get_document(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    return conn.execute(text(_SELECT), {"c": customer_id, "d": document_id}).mappings().first()


# PRD 5 Q1: one drop of files, the uploader's own only, out of Trash, in upload order.
# Any version: a file uploaded as a duplicate is not in it (it stays in its own batch).
# At most MAX_FILES_PER_BATCH rows, the upload's own cap (409 past it). PRD 5 U1: the tray
# says "Couldn't read page n" with the reason and the fix, so each row also has its first
# unreadable page still on the attention list, with what views.page_problems needs, or
# NULL (the pages key). And how many values were found in it, so the tray says "All values
# verified" only for a file that has some (the index extracted_values_record).
_VALUES_FOUND = (
    "(SELECT count(*) FROM extracted_values vf WHERE vf.customer_id = d.customer_id"
    " AND vf.document_id = d.id) AS values_found"
)
_FIRST_UNREADABLE = (
    "(SELECT json_build_object('page_no', pf.page_no, 'read_issues', pf.read_issues,"
    " 'illegible_words', pf.illegible_words, 'unpinned_lines', pf.unpinned_lines,"
    " 'effective_dpi', pf.effective_dpi)"
    " FROM pages pf WHERE pf.customer_id = d.customer_id AND pf.document_id = d.id"
    " AND pf.read_quality = 'unreadable' AND pf.accepted_at IS NULL"
    " ORDER BY pf.page_no LIMIT 1) AS first_unreadable"
)
_BATCH = (
    _COLUMNS + ", " + _FIRST_UNREADABLE + ", " + _VALUES_FOUND + " FROM documents d"
    " LEFT JOIN users u ON u.customer_id = d.customer_id AND u.id = d.uploaded_by"
    " WHERE d.customer_id = :c AND d.batch_id = :b AND d.uploaded_by = :u AND d.deleted_at IS NULL"
    " ORDER BY d.created_at, d.id LIMIT :limit"
)
_BATCH_SIZE = (
    "SELECT count(*) FROM documents d"
    " WHERE d.customer_id = :c AND d.batch_id = :b AND d.uploaded_by = :u AND d.deleted_at IS NULL"
)


def batch_rows(
    conn: Connection, customer_id: str, batch_id: str, *, user_id: str, limit: int
) -> Sequence[RowMapping]:
    """The DocumentSummary rows of ``user_id``'s batch ``batch_id``, at most ``limit``
    (MAX_FILES_PER_BATCH); empty for another user's or customer's batch, or one that is
    not there. On the index documents_batch: O(log D + b) for the batch's b files, each
    with _COLUMNS' probes."""
    params = {"c": customer_id, "b": batch_id, "u": user_id, "limit": limit}
    return conn.execute(text(_BATCH), params).mappings().all()


def batch_size(conn: Connection, customer_id: str, batch_id: str, *, user_id: str) -> int:
    """How many files ``user_id``'s batch holds, for the upload's cap (Q1). One count
    on the index documents_batch: O(log D + b)."""
    params = {"c": customer_id, "b": batch_id, "u": user_id}
    return int(conn.execute(text(_BATCH_SIZE), params).scalar_one())


def family_versions(conn: Connection, customer_id: str, family_id: str) -> Sequence[RowMapping]:
    """Every version of one family, the first version first, in Trash or not.
    One query on the index documents_family_version: O(log D + v) time and
    O(v) space for the family's v versions."""
    return conn.execute(text(_VERSIONS), {"c": customer_id, "f": family_id}).mappings().all()


# A PageSummary row: the page, how it was read (PRD 2 R1, R4) and its reading result
# (PRD 5 RT2, F1), "Keep as is" and a read again asked for (F5). PRD 5 U4: the values on
# the page the two readers still read differently, the count readers_disagree's sentence
# names (the index extracted_values_record). Model choice (council 11 Oct 2026): the
# catalogue name of the model that read it, ``read_by_name``; NULL for rapidocr, text_layer,
# cells or a model the catalogue never listed (a LEFT JOIN on its primary key).
_PAGE = (
    "SELECT page_no, width, height, unit, has_image, route, effective_dpi, low_resolution,"
    " read_by, mc.name AS read_by_name, read_quality, read_issues, unsure_words, illegible_words,"
    " unpinned_lines, accepted_by, accepted_at, read_again_requested_at,"
    " (SELECT count(*) FROM extracted_values ev WHERE ev.customer_id = pages.customer_id"
    " AND ev.document_id = pages.document_id AND ev.page_no = pages.page_no"
    " AND ev.label_reason = 'readers_disagree') AS values_disputed"
    " FROM pages LEFT JOIN model_catalog mc ON mc.id = pages.read_by"
)


def list_pages(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text(_PAGE + " WHERE customer_id = :c AND document_id = :d ORDER BY page_no"),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .all()
    )


def get_page(conn: Connection, customer_id: str, document_id: str, page_no: int) -> RowMapping | None:
    return (
        conn.execute(
            text(_PAGE + " WHERE customer_id = :c AND document_id = :d AND page_no = :n"),
            {"c": customer_id, "d": document_id, "n": page_no},
        )
        .mappings()
        .first()
    )


def reviewer_fix_count(conn: Connection, customer_id: str, document_id: str) -> int:
    """How many of this document's values a reviewer fixed (PRD 2 X1, said before
    "Read again"). A fix whose value a later read replaced has no value any more
    (``value_id`` NULL), and is not counted (AR15c). One query on the index
    corrections_document: O(log C + fixes)."""
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM corrections"
                " WHERE customer_id = :c AND document_id = :d AND value_id IS NOT NULL"
            ),
            {"c": customer_id, "d": document_id},
        ).scalar_one()
    )


def document_facts(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    """The detail's document card columns (PRD 2 D3): its date and card JSON. Kept out of
    ``_COLUMNS`` so the list stays small. One query by primary key."""
    return (
        conn.execute(
            text("SELECT document_date, card FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .one_or_none()
    )


_INVOICE = text(
    "SELECT id, invoice_number, invoice_date, seller_name, seller_gstin, buyer_name, buyer_gstin,"
    " subtotal, tax_total, total, currency, label, check_status, check_reasons"
    " FROM invoices WHERE customer_id = :c AND document_id = :d"
)
_INVOICE_LINES = text(
    "SELECT line_no, description, hsn, quantity, unit, rate, amount, label FROM invoice_lines"
    " WHERE customer_id = :c AND invoice_id = :i ORDER BY line_no"
)


def invoice_for(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[RowMapping, Sequence[RowMapping]] | None:
    """The detail's invoice (PRD 2 E5): the document's invoices row and its lines in
    line_no order, or None. Two indexed queries (the unique document_id, then the unique
    (invoice_id, line_no)); O(lines)."""
    params = {"c": customer_id, "d": document_id}
    row = conn.execute(_INVOICE, params).mappings().one_or_none()
    if row is None:
        return None
    lines = conn.execute(_INVOICE_LINES, {**params, "i": row["id"]}).mappings().all()
    return row, lines


# PRD 3 Y2: the documents linked to this one, either way round (the pipeline stores
# both ways, so DISTINCT shows each partner and identifier once), and only a
# current, ready one out of Trash.
_LINKS = text(
    "SELECT DISTINCT d.id, d.display_name, d.doc_type, l.identifier_kind, l.identifier_value"
    " FROM document_links l JOIN documents d ON d.customer_id = l.customer_id"
    " AND d.id = CASE WHEN l.document_id = :d THEN l.linked_document_id ELSE l.document_id END"
    " WHERE l.customer_id = :c AND (l.document_id = :d OR l.linked_document_id = :d)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " ORDER BY d.display_name, d.id, l.identifier_kind, l.identifier_value LIMIT :limit"
)
# ponytail: a cap, not paging; an identifier shared by more documents than this shows the first ones.
MAX_LINKS = 50


def document_links(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """The detail's related documents (PRD 3 Y2), by name. One query on the unique key's
    document_id and the index document_links_linked: O(L log L) for the document's L links."""
    params = {"c": customer_id, "d": document_id, "limit": MAX_LINKS}
    return conn.execute(_LINKS, params).mappings().all()


def list_values(conn: Connection, customer_id: str, document_id: str) -> Sequence[RowMapping]:
    """Every value of one document, by page, record and field. O(v log v)."""
    return (
        conn.execute(
            text(
                "SELECT id, document_id, record_key, field, value_text, value_raw, second_reading,"
                " page_no, region, label, label_reason, confidence, label_page_no, label_region, votes"
                " FROM extracted_values WHERE customer_id = :c AND document_id = :d"
                " ORDER BY page_no, record_key, field, id"
            ),
            {"c": customer_id, "d": document_id},
        )
        .mappings()
        .all()
    )


def sheet_cells(
    conn: Connection, customer_id: str, document_id: str, sheet: int, row_from: int, row_to: int
) -> Sequence[RowMapping]:
    """The cell texts of one sheet between two rows, from the first reader's
    stored output, ordered by row and column.

    Each stored span is ``{"text", "raw", "region": {...}, "confidence"}``
    (``kaagaz.db.pipeline_store.span_to_json``); only
    cell locations of this sheet count. O(spans of the document): the output is
    one jsonb array, unpacked in the database.
    """
    return (
        conn.execute(
            text(
                "SELECT (loc ->> 'row')::int AS row_no, (loc ->> 'column')::int AS column_no,"
                " span ->> 'text' AS text"
                " FROM reader_outputs ro"
                " CROSS JOIN LATERAL jsonb_array_elements(ro.output) AS span"
                " CROSS JOIN LATERAL (SELECT span -> 'region' AS loc) AS l"
                " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
                " AND loc ->> 'kind' = 'cell' AND (loc ->> 'page')::int = :sheet"
                " AND (loc ->> 'row')::int BETWEEN :row_from AND :row_to"
                " ORDER BY row_no, column_no"
            ),
            {"c": customer_id, "d": document_id, "sheet": sheet, "row_from": row_from, "row_to": row_to},
        )
        .mappings()
        .all()
    )


# Scanned text (2 Oct 2026): every line each reader read on one page, for the viewer
# only. This text goes to the screen and never to the model, so nothing under
# answering/ or search/ may import page_readings (tests/test_api_readings.py checks).
MAX_READING_LINES = 2000  # per reader and page
MAX_READING_TEXT = 100_000  # characters per reader and page
MAX_READING_CHARS = MAX_READING_TEXT  # one line, cut in SQL: never more than the whole page
READINGS_TIMEOUT_MS = 3000
_SET_READINGS_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")
# One row for a reader with no line on the page (the LEFT JOIN), else one per line in
# stored order (ORDINALITY). A reader's lines stop once the characters before a line
# pass the budget, so at most one line over it leaves the database; Python drops that
# one and says ``truncated`` (views.page_readings).
_PAGE_READINGS = text(
    "SELECT ro.role, ro.reader, ro.reader_version, l.ord, l.text, l.cut, l.confidence, l.origin,"
    " l.kind, l.region, l.table_id, l.table_row, l.table_col, l.table_header"
    " FROM reader_outputs ro"
    " LEFT JOIN LATERAL (SELECT * FROM (SELECT e.ord, left(e.span ->> 'raw', :max_chars) AS text,"
    " e.span ->> 'table_id' AS table_id, CAST(e.span ->> 'row' AS integer) AS table_row,"
    " CAST(e.span ->> 'col' AS integer) AS table_col, CAST(e.span ->> 'header' AS boolean) AS table_header,"
    " length(e.span ->> 'raw') > :max_chars AS cut,"
    " CAST(e.span ->> 'confidence' AS double precision) AS confidence,"
    " e.span ->> 'origin' AS origin, e.span ->> 'kind' AS kind, e.span -> 'region' AS region,"
    " sum(length(left(e.span ->> 'raw', :max_chars))) OVER (ORDER BY e.ord) AS upto"
    " FROM jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE (e.span -> 'region' ->> 'page')::int = :page_no"
    " AND NOT (coalesce(e.span ->> 'origin', '') = 'ocr' AND e.span ->> 'kind' IS NULL"
    " AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
    " ORDER BY e.ord LIMIT :max_lines + 1) AS s"
    " WHERE s.upto - length(s.text) <= :max_text) AS l ON true"
    " WHERE ro.customer_id = :c AND ro.document_id = :d"
    " ORDER BY ro.role, l.ord"
)


def page_readings(conn: Connection, customer_id: str, document_id: str, page_no: int) -> Sequence[RowMapping]:
    """Every line the first and second reader read on one page, as read (``raw``),
    the first reader first, each in stored order; capped in SQL, under a local timeout
    (set_config(..., true) lasts until this transaction ends, as in search/hybrid.py).

    O(spans of the document), as ``sheet_cells``: the output is one jsonb array,
    unpacked in the database, and only this page's lines leave it.
    ponytail: a per-page index of the spans is the upgrade if big documents get slow.
    """
    conn.execute(_SET_READINGS_TIMEOUT, {"ms": str(READINGS_TIMEOUT_MS)})
    params = {
        "c": customer_id,
        "d": document_id,
        "page_no": page_no,
        "max_lines": MAX_READING_LINES,
        "max_chars": MAX_READING_CHARS,
        "max_text": MAX_READING_TEXT,
    }
    return conn.execute(_PAGE_READINGS, params).mappings().all()


# PRD 5 U3: the reading problems drawn on one page, from the stored output: each word
# mark (a span read by OCR with no kind and quality unsure or illegible, F3) and each
# line whose spot no found box confirmed (box_source "model", G5: "unpinned"), in stored
# order, at most MAX_READING_LINES, each text cut to MAX_READING_CHARS. For the viewer
# only, like page_readings.
_PAGE_MARKS = text(
    "SELECT CASE WHEN e.span ->> 'kind' IS NULL THEN e.span ->> 'quality' ELSE 'unpinned' END AS quality,"
    " e.span -> 'region' AS region, left(e.span ->> 'text', :max_chars) AS text"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d"
    " AND (e.span -> 'region' ->> 'page')::int = :page_no AND e.span ->> 'origin' = 'ocr'"
    " AND ((e.span ->> 'kind' IS NULL AND e.span ->> 'quality' IN ('unsure', 'illegible'))"
    " OR (e.span ->> 'kind' IS NOT NULL AND e.span ->> 'box_source' = 'model'))"
    " ORDER BY ro.role, e.ord LIMIT :max_marks"
)


def page_marks(conn: Connection, customer_id: str, document_id: str, page_no: int) -> Sequence[RowMapping]:
    """The page's reading marks (quality, region, text): unsure, illegible or unpinned.
    Call it after ``page_readings`` in the same transaction, so its timeout holds.
    O(spans of the document), as ``page_readings``."""
    params = {
        "c": customer_id,
        "d": document_id,
        "page_no": page_no,
        "max_chars": MAX_READING_CHARS,
        "max_marks": MAX_READING_LINES,
    }
    return conn.execute(_PAGE_MARKS, params).mappings().all()


# Your words on the page (package H): the document by id as the detail opens it (any
# version, in Trash or not, which the route then refuses as the page image does), and its
# pieces' text, Latin spelling and regions in seq order (the index pieces_document), for
# kaagaz/search/page_words.py. No row: no such document; one row of NULL text: no pieces.
_WORD_LINES = text(
    "SELECT d.deleted_at IS NOT NULL, d.status, p.text, p.text_latin, p.regions FROM documents d"
    " LEFT JOIN pieces p ON p.customer_id = :c AND p.document_id = d.id"
    " WHERE d.customer_id = :c AND d.id = :d ORDER BY p.seq"
)


def word_lines(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[bool, str, list[tuple[str, str | None, list[Any]]]] | None:
    """Whether the document is in Trash, its status, and (text, text_latin, regions) of every
    piece of it; None when there is no such document for this customer. One statement:
    O(log N + the document's pieces)."""
    rows = conn.execute(_WORD_LINES, {"c": customer_id, "d": document_id}).all()
    if not rows:
        return None
    pieces = [(str(row[2]), row[3], list(row[4])) for row in rows if row[2] is not None]
    return bool(rows[0][0]), str(rows[0][1]), pieces


# Read clearly (19 Oct 2026): the whole document's main reader lines (its first role) in one
# unpacking, page by page in stored order, under a document-wide cap: at most
# CLEAR_COPY_LINES lines and about CLEAR_COPY_TEXT characters (one line over leaves the
# database, as in _PAGE_READINGS; views.clear_copy drops it and says ``truncated``).
CLEAR_COPY_LINES = 20_000
CLEAR_COPY_TEXT = 1_000_000
_CLEAR_COPY = text(
    "SELECT * FROM (SELECT (e.span -> 'region' ->> 'page')::int AS page_no, e.ord,"
    " left(e.span ->> 'raw', :max_chars) AS text, length(e.span ->> 'raw') > :max_chars AS cut,"
    " CAST(e.span ->> 'confidence' AS double precision) AS confidence,"
    " e.span ->> 'origin' AS origin, e.span ->> 'kind' AS kind, e.span -> 'region' AS region,"
    " e.span ->> 'table_id' AS table_id, CAST(e.span ->> 'row' AS integer) AS table_row,"
    " CAST(e.span ->> 'col' AS integer) AS table_col, CAST(e.span ->> 'header' AS boolean) AS table_header,"
    " sum(length(left(e.span ->> 'raw', :max_chars)))"
    " OVER (ORDER BY (e.span -> 'region' ->> 'page')::int, e.ord) AS upto"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = (SELECT min(role) FROM reader_outputs"
    " WHERE customer_id = :c AND document_id = :d)"
    " AND NOT (coalesce(e.span ->> 'origin', '') = 'ocr' AND e.span ->> 'kind' IS NULL"
    " AND coalesce(e.span ->> 'quality', '') IN ('unsure', 'illegible'))"
    " ORDER BY (e.span -> 'region' ->> 'page')::int, e.ord LIMIT :max_lines + 1) AS s"
    " WHERE s.upto - length(s.text) <= :max_text ORDER BY s.page_no, s.ord"
)
# Its marks, as _PAGE_MARKS for every page in one unpacking, at most CLEAR_COPY_LINES.
_CLEAR_COPY_MARKS = text(
    "SELECT (e.span -> 'region' ->> 'page')::int AS page_no,"
    " CASE WHEN e.span ->> 'kind' IS NULL THEN e.span ->> 'quality' ELSE 'unpinned' END AS quality,"
    " e.span -> 'region' AS region, left(e.span ->> 'text', :max_chars) AS text"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND e.span ->> 'origin' = 'ocr'"
    " AND ((e.span ->> 'kind' IS NULL AND e.span ->> 'quality' IN ('unsure', 'illegible'))"
    " OR (e.span ->> 'kind' IS NOT NULL AND e.span ->> 'box_source' = 'model'))"
    " ORDER BY ro.role, e.ord LIMIT :max_lines"
)


def clear_copy(
    conn: Connection, customer_id: str, document_id: str
) -> tuple[Sequence[RowMapping], Sequence[RowMapping]]:
    """The document's lines and marks for Read clearly: two statements, each unpacking the
    stored output once, under the readings' timeout. O(spans of the document)."""
    conn.execute(_SET_READINGS_TIMEOUT, {"ms": str(READINGS_TIMEOUT_MS)})
    params = {
        "c": customer_id,
        "d": document_id,
        "max_lines": CLEAR_COPY_LINES,
        "max_chars": MAX_READING_CHARS,
        "max_text": CLEAR_COPY_TEXT,
    }
    lines = conn.execute(_CLEAR_COPY, params).mappings().all()
    return lines, conn.execute(_CLEAR_COPY_MARKS, params).mappings().all()


def rename_document(conn: Connection, customer_id: str, document_id: str, *, name: str) -> None:
    """Give this one version a new display name, already cleaned by the caller, and
    put it in its pieces' context headers too, so search never shows the old name.
    O(log D + P) for the document's P pieces."""
    params = {"c": customer_id, "d": document_id}
    old = conn.execute(text(_NAME_OF), params).scalar_one_or_none()
    conn.execute(text(_RENAME), {**params, "name": name, "key": name_key(name)})  # V2
    if old is not None:
        headers = {"old": document_part(old), "new": document_part(name)}
        conn.execute(text(_RENAME_HEADERS), {**params, **headers})


# --- the dashboard (File management D2) ----------------------------------------------
# Plain counts over the current, untrashed documents (the documents_current index), with the
# documents_listing index for the week and month and the recent list.
_DASHBOARD_TILES = text(
    "SELECT (SELECT count(*) FROM documents d WHERE d.customer_id = :c"  # noqa: S608 - visibility.LIVE_SUGGESTION
    " AND d.deleted_at IS NULL AND d.is_current) AS documents,"
    " (SELECT count(*) FROM documents d"
    " JOIN documents r ON r.customer_id = d.customer_id AND r.id = d.family_id"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND r.created_at >= :week) AS this_week,"
    " (SELECT count(*) FROM documents d WHERE d.customer_id = :c AND d.deleted_at IS NULL"
    " AND d.version_no > 1 AND d.created_at >= :month) AS new_versions,"
    f" (SELECT count(*) FROM version_suggestions s WHERE s.customer_id = :c AND {LIVE_SUGGESTION})"
    " AS to_confirm"
)
_DASHBOARD_TYPES = text(
    "SELECT doc_type, count(*) AS n FROM documents"
    " WHERE customer_id = :c AND is_current AND deleted_at IS NULL"
    " GROUP BY doc_type ORDER BY n DESC, doc_type NULLS LAST"
)
# Each department with the current, untrashed documents anywhere below it (100 levels at most).
_DASHBOARD_DEPARTMENTS = text(
    "WITH RECURSIVE tree (id, root, depth) AS ("
    " SELECT id, id, 1 FROM folders WHERE customer_id = :c AND parent_id IS NULL AND kind = 'department'"
    " UNION ALL SELECT f.id, t.root, t.depth + 1 FROM tree t JOIN folders f ON f.parent_id = t.id"
    " WHERE f.customer_id = :c AND t.depth < 101)"
    " SELECT dep.id, dep.name, dep.sort_order, count(d.id) AS files FROM folders dep"
    " JOIN tree t ON t.root = dep.id"
    " LEFT JOIN documents d ON d.customer_id = :c AND d.folder_id = t.id"
    " AND d.deleted_at IS NULL AND d.is_current WHERE dep.customer_id = :c"
    " GROUP BY dep.id ORDER BY dep.sort_order NULLS LAST, lower(dep.name), dep.id"
)
_DASHBOARD_RECENT = text(
    "SELECT id, display_name, doc_type, sensitivity, version_no, status, created_at FROM documents"
    " WHERE customer_id = :c AND is_current AND deleted_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 10"
)


def dashboard(conn: Connection, customer_id: str, *, week: datetime, month: datetime) -> dict[str, Any]:
    """D2: the tiles (documents, those added since ``week``, versions added since ``month``,
    suggestions to confirm), documents by type, each department's files and the files in
    none and the 10 newest documents (the last 20 activities: ``audit.entries``, A5). Four
    statements, each on an index or a count over this customer's current documents: O(D)."""
    params = {"c": customer_id, "week": week, "month": month}
    tiles = conn.execute(_DASHBOARD_TILES, params).mappings().one()
    departments = conn.execute(_DASHBOARD_DEPARTMENTS, params).mappings().all()
    return {
        "tiles": dict(tiles),
        "by_type": conn.execute(_DASHBOARD_TYPES, params).mappings().all(),
        "departments": departments,
        "not_in_department": int(tiles["documents"]) - sum(int(row["files"]) for row in departments),
        "recent": conn.execute(_DASHBOARD_RECENT, params).mappings().all(),
    }


# File management O1 to O3, V9: the owner and label of the whole family, this version's note,
# and the family's details stamp, in one statement; a field not given keeps its value.
_SET_DETAILS = (
    "UPDATE documents SET"
    " owner_id = CASE WHEN :set_owner THEN CAST(:owner AS uuid) ELSE owner_id END,"
    " sensitivity = CASE WHEN :set_label THEN CAST(:label AS text) ELSE sensitivity END,"
    " sensitivity_set_by_person = sensitivity_set_by_person OR :set_label,"
    " version_note = CASE WHEN :set_note AND id = :d THEN CAST(:note AS text) ELSE version_note END,"
    " details_updated_by = :u, details_updated_at = now()"
    " WHERE customer_id = :c"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
)
_ACTIVE_USER = "SELECT 1 FROM users WHERE customer_id = :c AND id = :u AND NOT disabled"
_UNSET: Any = object()


def active_user(conn: Connection, customer_id: str, user_id: str) -> bool:
    """O1: an owner is an active user of this customer. One lookup by key."""
    return conn.execute(text(_ACTIVE_USER), {"c": customer_id, "u": user_id}).first() is not None


def set_details(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    by: str,
    owner_id: str | None = _UNSET,
    sensitivity: str | None = _UNSET,
    version_note: str | None = _UNSET,
) -> None:
    """Set what is given (O1 owner and O2 label on every version of the family, V9 this
    version's note) and stamp the family's details as changed by ``by`` now (O3); with
    nothing given, the stamp alone (a rename, a move, a tag change). The caller holds the
    family lock. One statement: O(v log D) for the family's v versions."""
    params = {
        "c": customer_id,
        "d": document_id,
        "u": by,
        "set_owner": owner_id is not _UNSET,
        "owner": None if owner_id is _UNSET else owner_id,
        "set_label": sensitivity is not _UNSET,
        "label": None if sensitivity is _UNSET else sensitivity,
        "set_note": version_note is not _UNSET,
        "note": None if version_note is _UNSET else version_note,
    }
    conn.execute(text(_SET_DETAILS), params)


def move_family(conn: Connection, customer_id: str, document_id: str, *, folder_id: str | None) -> None:
    """File every version of the document's family in ``folder_id`` (None: the
    top level). The caller holds the family lock (``trash.lock_family``).

    Raises FolderNotFound when the folder is not this customer's: the foreign
    key refuses it, and the caller's transaction must end. A folder Kaagaz made that
    the move leaves empty is deleted (``prune_empty``). O(v log D) for the family's
    v versions.
    """
    params = {"c": customer_id, "d": document_id, "f": folder_id}
    left = [str(f) for f in conn.execute(text(_FAMILY_FOLDERS), params).scalars() if str(f) != folder_id]
    try:
        conn.execute(text(_MOVE_FAMILY), params)
    except IntegrityError as error:
        if folder_missing(error):
            raise FolderNotFound() from None
        raise
    prune_empty(conn, customer_id, left)


# --- Model choice and AI spend (council 11 Oct 2026, MS5, SP2, SP4) -------------------

# Features 26 and 27 (0026): a mode given replaces read_also too, in the same statement, so
# the ai_documents_read_mode_models check always sees a pair it allows.
_SET_READING = (
    "UPDATE documents SET read_requested_by = :u,"
    " read_model = COALESCE(CAST(:m AS text), read_model),"
    " read_mode = COALESCE(CAST(:mode AS text), read_mode),"
    " read_also = CASE WHEN CAST(:mode AS text) IS NULL THEN read_also ELSE CAST(:also AS text[]) END"
    " WHERE customer_id = :c AND id = :d"
)


def set_reading(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    requested_by: str,
    model: str | None,
    mode: str | None = None,
    also: Sequence[str] = (),
) -> None:
    """Who asked for the document's next reading, the user its scan spend is billed to, and
    the reading chosen (already checked by ``api.readers.check_reading``): ``model`` None
    keeps the document's own model, ``mode`` None its mode and other readers (``also``,
    Ensemble's). O(log D)."""
    params = {
        "c": customer_id,
        "d": document_id,
        "u": requested_by,
        "m": model,
        "mode": mode,
        "also": list(also),
    }
    conn.execute(text(_SET_READING), params)


def ai_budget(conn: Connection, customer_id: str) -> float | None:
    """The organisation's monthly AI budget in US dollars, set by the operator; None: none."""
    value = conn.execute(
        text("SELECT ai_budget_usd FROM customers WHERE id = :c"), {"c": customer_id}
    ).scalar_one_or_none()
    return None if value is None else float(value)


# The spend CSV (SP2): one line per day in TIMEZONE, user, model and kind. The user is
# grouped by id, so two users with one name stay apart; a NULL user (work no user asked
# for, or a removed user) has no name. The index model_usage_period.
_USAGE_ROWS = (
    "SELECT (u.created_at AT TIME ZONE :tz)::date AS day, max(us.display_name) AS user_name,"
    " u.model, u.kind, sum(u.calls) AS calls, sum(u.input_tokens + u.output_tokens) AS tokens,"
    " sum(u.cost_usd) AS cost_usd, bool_or(u.cost_estimated) AS cost_estimated,"
    " bool_or(u.cost_usd IS NULL) AS cost_part_unknown"
    " FROM model_usage u"
    " LEFT JOIN users us ON us.customer_id = u.customer_id AND us.id = u.user_id"
    " WHERE u.customer_id = :c AND u.created_at >= :start AND u.created_at < :end"
)
_USAGE_ROWS_ORDER = " GROUP BY 1, u.user_id, u.model, u.kind ORDER BY 1, 2 NULLS FIRST, u.model, u.kind"


def usage_rows(
    conn: Connection,
    customer_id: str,
    *,
    start: datetime,
    end: datetime,
    timezone: str,
    user_id: str | None,
) -> Sequence[RowMapping]:
    """The ledger from ``start`` to ``end`` (not included), for one user when given, one row
    per day, user, model and kind. O(rows in the period)."""
    statement = _USAGE_ROWS + ("" if user_id is None else " AND u.user_id = :u") + _USAGE_ROWS_ORDER
    params = {"c": customer_id, "start": start, "end": end, "tz": timezone, "u": user_id}
    return conn.execute(text(statement), params).mappings().all()
