"""The version finder (V17, #648, V-B): Kaagaz's one look for possible versions among the files an
organisation already has, in the worker, with no person (it reads tables, never the access views,
#595 condition 1) and no model call.

* **Once per organisation** (``version_finder_runs``), and only while the ``version_spotting``
  switch is on for the platform and the organisation and none of its ready files is waiting for its
  fingerprint (the fingerprint sweep comes first). A finished run never starts again; files uploaded
  after the run began are spotting's (``spot``), never the finder's.
* **Resumable and bounded**: the files are looked at in id order, each in its own transaction with
  its own statement timeout (STATEMENT_MS), which also moves the run's cursor (``after_id``); a
  restart goes on after the last one.
* **The pairs**: each file is scored against the files with a higher id as spotting scores a new
  upload (``versions_sql.scored``: a shared fingerprint band, a similar name or a related-documents
  link; the text overlap counted exactly, V4's rule), among its organisation's current, ready files
  out of Trash in other families (a locked file is never ready, 0093), never a pair spotting
  already suggested or a person already answered. The file looked at is taken FOR SHARE first, as
  the pipeline's ``still_open`` does, so a lock under way is waited for and then seen. Its
  candidates are scored with no row lock (review, 16 Nov 2026: a lock or a move never waits on up
  to 200 of them); then only the scored files (at most MAX_CANDIDATES) are taken FOR SHARE, in id
  order, and checked again (``_LIVE``) before any pair is written, so no pair is written for a file
  locked or trashed meanwhile, nor after a lock cleared its pairs. As in spotting, a candidate's
  text can be read in the moment before a lock commits; nothing from it is kept.
* **The groups** (``finish``): pairs whose two files are still live join into groups
  (``versions.groups``: strongest first, at most MAX_GROUP files), each file's place by its own
  date, else its upload date; the last is the suggested latest. Nothing is ever linked.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from kaagaz import versions
from kaagaz.db import ai_settings
from kaagaz.db import versions as versions_sql

# Each statement of the finder ends within this (it runs between jobs, on idle turns).
STATEMENT_MS = 30_000

_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")
_RUN = text("SELECT finished_at IS NOT NULL FROM version_finder_runs WHERE customer_id = :c")
_UNPRINTED = text(
    "SELECT EXISTS (SELECT 1 FROM documents WHERE customer_id = :c AND status = 'ready'"
    " AND text_minhash IS NULL)"
)
_START = text(
    "INSERT INTO version_finder_runs (customer_id) VALUES (:c) ON CONFLICT (customer_id) DO NOTHING"
)
_CURSOR = text(
    "SELECT after_id, started_at, finished_at IS NOT NULL AS finished FROM version_finder_runs"
    " WHERE customer_id = :c FOR UPDATE"
)
# The next files to look at: current, ready (never locked, 0093), out of Trash, uploaded before the run.
_NEXT = text(
    "SELECT id FROM documents WHERE customer_id = :c"
    " AND deleted_at IS NULL AND is_current AND status = 'ready' AND created_at < :started"
    " AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid))"
    " ORDER BY id LIMIT :n"
)
_ME = text(
    "SELECT text_minhash FROM documents WHERE customer_id = :c AND id = :d AND deleted_at IS NULL"
    " AND is_current AND status = 'ready' FOR SHARE"
)
# Spotting's candidates (versions_sql._CANDIDATES) with a higher id, each pair looked at once.
_CANDIDATES = text(
    "SELECT d.id, d.text_minhash, similarity(d.name_key, me.name_key) AS name_similarity,"
    " (SELECT min(l.identifier_kind) FROM document_links l WHERE l.customer_id = :c AND l.document_id = me.id"
    " AND l.linked_document_id = d.id AND d.doc_type = me.doc_type) AS shared_identifier"
    " FROM documents me JOIN documents d ON d.customer_id = me.customer_id"
    " WHERE me.customer_id = :c AND me.id = :d AND d.id > me.id AND d.family_id <> me.family_id"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready' AND d.created_at < :started"
    " AND (d.text_bands && me.text_bands"
    " OR (d.name_key % me.name_key AND similarity(d.name_key, me.name_key) >= :name)"
    " OR EXISTS (SELECT 1 FROM document_links l WHERE l.customer_id = :c AND l.document_id = me.id"
    " AND l.linked_document_id = d.id))"
    " AND NOT EXISTS (SELECT 1 FROM version_suggestions s WHERE s.customer_id = :c"
    " AND ((s.document_id = me.id AND s.candidate_family_id = d.family_id)"
    " OR (s.document_id = d.id AND s.candidate_family_id = me.family_id)))"
    " ORDER BY (d.text_bands && me.text_bands) DESC, name_similarity DESC NULLS LAST, d.id"
    " LIMIT :n"
)
_PAIR = text(
    "INSERT INTO version_pairs (customer_id, first_id, second_id, text_overlap, text_overlap_exact,"
    " name_similarity, shared_identifier) VALUES (:c, :a, :b, :t, :exact, :n, :i) ON CONFLICT DO NOTHING"
)
_ADVANCE = text(
    "UPDATE version_finder_runs SET after_id = :d, looked_at = looked_at + 1 WHERE customer_id = :c"
)
_PAIRS = text(
    "SELECT first_id::text, second_id::text, text_overlap, name_similarity, shared_identifier IS NOT NULL"
    " FROM version_pairs WHERE customer_id = :c"
)
# The paired files still live, taken FOR SHARE (a lock under way is waited for), with each one's date.
_LIVE = text(
    "SELECT id::text, coalesce(CAST(document_date AS timestamptz), created_at), created_at FROM documents"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL AND is_current"
    " AND status = 'ready' ORDER BY id FOR SHARE"
)
_MEMBER = text(
    "INSERT INTO version_group_members (customer_id, document_id, group_id, position) VALUES (:c, :d, :g, :p)"
)
_FINISH = text("UPDATE version_finder_runs SET finished_at = now(), groups = :n WHERE customer_id = :c")


def finished(conn: Connection, customer_id: str) -> bool:
    """Whether the organisation's run has finished (never started: no). One statement."""
    return bool(conn.execute(_RUN, {"c": customer_id}).scalar())


def due(conn: Connection, customer_id: str) -> bool:
    """Whether the organisation's run is still to do: not finished, its ``version_spotting`` switch
    on, and none of its ready files waiting for a fingerprint. Three statements."""
    if finished(conn, customer_id) or not ai_settings.switch_on(conn, versions_sql.FEATURE, customer_id):
        return False
    return not conn.execute(_UNPRINTED, {"c": customer_id}).scalar_one()


def _run(conn: Connection, customer_id: str) -> tuple[Any, datetime, bool]:
    """The run, started when there is none, row-locked: (after_id, started_at, finished)."""
    conn.execute(_TIMEOUT, {"ms": str(STATEMENT_MS)})
    conn.execute(_START, {"c": customer_id})
    after, started, finished = conn.execute(_CURSOR, {"c": customer_id}).one()
    return after, started, bool(finished)


def next_documents(conn: Connection, customer_id: str, limit: int) -> Sequence[str] | None:
    """The next ``limit`` files to look at, after the run's cursor (the run started when there is
    none); None once the run has finished. O(limit log D)."""
    after, started, finished = _run(conn, customer_id)
    if finished:
        return None
    rows = conn.execute(_NEXT, {"c": customer_id, "started": started, "after": after, "n": limit})
    return [str(row[0]) for row in rows]


def look_at(conn: Connection, customer_id: str, document_id: str) -> int:
    """Score one file against the files with a higher id (module notes), store the pairs V4
    suggests, and move the run's cursor past it; how many pairs it stored. O(versions_sql.scored)."""
    _, started, _ = _run(conn, customer_id)
    params = {"c": customer_id, "d": document_id}
    stored = 0
    me = conn.execute(_ME, params).first()
    if me is not None:
        fetched = conn.execute(
            _CANDIDATES,
            {**params, "started": started, "name": versions.NAME_SIGNAL, "n": versions_sql.MAX_FETCHED},
        ).mappings()
        found = versions_sql.scored(conn, customer_id, document_id, me[0], fetched)
        # The scored files alone (at most MAX_CANDIDATES), FOR SHARE in id order and checked again,
        # before anything is written: one locked or trashed since is never paired.
        scored_ids = sorted(str(row["id"]) for row, *_ in found)
        live = {row[0] for row in conn.execute(_LIVE, {"c": customer_id, "ids": scored_ids})}
        for row, overlap, exact, name in found:
            if str(row["id"]) not in live:
                continue
            pair = {"a": document_id, "b": str(row["id"]), "t": overlap, "exact": exact, "n": name}
            conn.execute(_PAIR, {**pair, "c": customer_id, "i": row["shared_identifier"]})
            stored += 1
    conn.execute(_ADVANCE, params)
    return stored


def pass_over(conn: Connection, customer_id: str, document_id: str) -> None:
    """Move the run's cursor past a file whose look keeps failing (the worker's FindVersions), so it
    never stalls the run; the file is in no pair. One statement."""
    conn.execute(_ADVANCE, {"c": customer_id, "d": document_id})


def finish(conn: Connection, customer_id: str) -> int:
    """Make the groups from the run's pairs whose files are still live (module notes) and finish
    the run, in one transaction; how many groups. O(p log p) for the p pairs."""
    conn.execute(_TIMEOUT, {"ms": str(STATEMENT_MS)})
    pairs = conn.execute(_PAIRS, {"c": customer_id}).all()
    ids = sorted({file for pair in pairs for file in pair[:2]})
    dates = {row[0]: (row[1], row[2], row[0]) for row in conn.execute(_LIVE, {"c": customer_id, "ids": ids})}
    found = versions.groups(
        (first, second, (overlap or 0.0, name or 0.0, shared))
        for first, second, overlap, name, shared in pairs
        if first in dates and second in dates
    )
    for group in found:
        group_id = str(uuid.uuid4())
        for position, document in enumerate(sorted(group, key=dates.__getitem__), 1):
            conn.execute(_MEMBER, {"c": customer_id, "d": document, "g": group_id, "p": position})
    conn.execute(_FINISH, {"c": customer_id, "n": len(found)})
    return len(found)
