"""Opening a locked file (PRD 7 section 6, #605, PR 13): the file through the access view, its page
pictures and the throttled ``lock.view`` row.

The API alone imports this module: ``_DOCUMENT`` and ``_PICTURE`` read ``visible_documents``,
which the worker must never reach (#595 condition 1). Every statement takes a connection from
``Database.user(customer_id, user_id)`` and names the customer too (two locks).
"""

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import audit

VIEW_EVERY_MINUTES = 10  # one lock.view per person, file and 10-minute check (plan question 13)

# R0 and R1 to R5 through the view: a file the person may not open is not here at all.
_DOCUMENT = text(
    "SELECT d.status, d.locked_at IS NOT NULL AS locked, d.deleted_at IS NOT NULL AS in_trash, d.page_count"
    " FROM visible_documents d WHERE d.customer_id = :c AND d.id = :d"
)
_PICTURE = text(
    "SELECT has_image FROM pages WHERE customer_id = :c AND document_id = :d AND page_no = :n"
    " AND EXISTS (SELECT 1 FROM visible_documents sd WHERE sd.customer_id = :c AND sd.id = :d)"
)
# One check of one person on one file at a time (as audit.record_view): two pictures at once write one row.
_VIEW_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('lock.view:' || :c || ':' || :u || ':' || :d, 0))"
)
# On audit_log_access (0090), the organisation's access and lock entries, newest first.
_VIEWED = text(
    "SELECT 1 FROM audit_log WHERE customer_id = :c AND action LIKE 'lock.%' AND action = 'lock.view'"
    " AND actor_user_id = :u AND target_id = :d AND created_at > now() - make_interval(mins => :m) LIMIT 1"
)


def document(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    """The file as the person sees it (status, locked, in_trash, page_count); None: not one they
    may open, which answers as an id that never was."""
    return conn.execute(_DOCUMENT, {"c": customer_id, "d": document_id}).mappings().first()


def has_picture(conn: Connection, customer_id: str, document_id: str, page_no: int) -> bool:
    found = conn.execute(_PICTURE, {"c": customer_id, "d": document_id, "n": page_no}).scalar()
    return bool(found)


def record_view(conn: Connection, customer_id: str, user_id: str, document_id: str) -> bool:
    """``lock.view`` about the file, unless this person had one in the last VIEW_EVERY_MINUTES (the
    database's clock, as the row's ``created_at``). True when a row was written."""
    params = {"c": customer_id, "u": user_id, "d": document_id}
    conn.execute(_VIEW_LOCK, params)
    if conn.execute(_VIEWED, {**params, "m": VIEW_EVERY_MINUTES}).first() is not None:
        return False
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action="lock.view",
        target_type="document",
        target_id=document_id,
    )
    return True
