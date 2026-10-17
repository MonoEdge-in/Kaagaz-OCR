"""Auto-filing (feature 28, council 15 Oct 2026): a new upload nobody put in a folder
goes into the folder for its type once it is read, like Invoices or Bank statements.

Why here: the filing rules (R1 to R18 of the council's plan) next to their SQL. Every
function takes a connection from ``Database.customer(customer_id)`` and also filters on
``customer_id`` (two locks, docs/architecture.md).

A family's state is ``documents.autofile`` on its first version (id = family_id):
``pending`` at upload (``PgDocuments.add_if_absent``, only with no folder and the switch
on), then ``filed`` or ``skipped`` once, or cleared to NULL. Every write of ``folder_id``
clears it (``listing.move_family``, the folder delete's detach), so a person's choice
always wins, and nothing is moved twice.

``file_document`` runs after ``mark_ready``, in its own transaction
(``PgDocuments.file_document``). It takes the tree lock, then the family lock: the order
of folder create, move and delete, so it queues behind them instead of deadlocking. Under
the locks the folder is the one holding the type's key wherever it now is, else a
top-level folder of the same name (any letter case) with no key, which is adopted, else a
new top-level folder owned by the uploader. Nothing is inserted to be caught: the unique
index folders_autofile_key is only the backstop. No model is called: the type is the
document card's (``documents.doc_type``).

Party folders (backlog 28b, final polish 16 Oct 2026): a verified invoice goes one level
deeper, into a folder for the other party inside the type folder ("Invoices / Mehta
Textiles"). Part of auto-filing, no switch of its own. Grouped by the other party's GSTIN
(checksum-valid, ``checks.gstin_valid``), never by a name: the folder's key is
``party:<GSTIN>``, used wherever the folder now is. The other party is the side that is
not the organisation's own GSTIN, so a seller/buyer swap by the reader still lands in the
same party's folder. There is no per-organisation GSTIN setting, so its own is the GSTIN
on the most of its verified invoices (``own_gstin``); when that is not clear, or neither
side of the invoice is it, the file goes in the type folder. The folder is named after the
other party as printed (``party_name``); a sibling of that name already there gets
" (<GSTIN>)" added, and when that is taken too, or there is no room (depth 5) or nobody to
own a new folder, the type folder is used. Undo still moves the family to the top level.

``undo`` moves a filed family back to the top level; the folder stays.
"""

import unicodedata
from collections.abc import Sequence

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import ai_settings, audit
from kaagaz.db.folders import _DEPTH, _MAX_DEPTH, _lock_tree
from kaagaz.db.trash import InTrash, lock_live_family
from kaagaz.extraction.checks import gstin_valid

PENDING, FILED, SKIPPED = "pending", "filed", "skipped"

# R7: the card's type and its folder. A type left out (other, or one that fails the
# council's per-type precision check before launch) is never filed.
FOLDERS = {
    "invoice": "Invoices",
    "bank_statement": "Bank statements",
    "receipt": "Receipts",
    "register": "Registers",
    "form": "Forms",
    "land_record": "Land records",
    "contract": "Contracts",
    "letter": "Letters",
}

# The family's state is on its first version, r.
_PRE_CHECK = (
    "SELECT r.autofile FROM documents d"
    " JOIN documents r ON r.customer_id = d.customer_id AND r.id = d.family_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)
# Everything decided under the locks, in one statement.
_STATE = (
    "SELECT r.autofile, d.family_id, d.status, d.is_current, d.doc_type, d.uploaded_by,"
    " c.status AS org_status, c.autofile_on, c.autofile_granted FROM documents d"
    " JOIN documents r ON r.customer_id = d.customer_id AND r.id = d.family_id"
    " JOIN customers c ON c.id = d.customer_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)
_SET_STATE = "UPDATE documents SET autofile = :state WHERE customer_id = :c AND id = :f"
_BY_KEY = "SELECT id FROM folders WHERE customer_id = :c AND autofile_key = :k"
# At most one: the unique index folders_sibling_name.
_BY_NAME = (
    "SELECT id, autofile_key FROM folders"
    " WHERE customer_id = :c AND parent_id IS NULL AND lower(name) = lower(:name)"
)
_ADOPT = "UPDATE folders SET autofile_key = :k WHERE customer_id = :c AND id = :f"
_CREATE = (
    "INSERT INTO folders (customer_id, parent_id, name, created_by, autofile_key)"
    " VALUES (:c, :p, :name, :u, :k) RETURNING id"
)
# The family into a folder (filed) or back to the top level (undone), its state with it.
_FILE_FAMILY = (
    "UPDATE documents SET folder_id = :folder,"
    " autofile = CASE WHEN id = family_id THEN CAST(:state AS text) END"
    " WHERE customer_id = :c AND family_id = :f"
)
_FILED_IN = "SELECT autofile, folder_id FROM documents WHERE customer_id = :c AND id = :f"

# --- party folders (28b) ---
PARTY_NAME_MAX = 80
OWN_MIN_INVOICES = 3
_INVOICE = (
    "SELECT seller_name, seller_gstin, buyer_name, buyer_gstin FROM invoices"
    " WHERE customer_id = :c AND document_id = :d AND label = 'verified'"
)
# The two GSTINs on the most verified invoices (either side, an invoice counted once per
# GSTIN), and how many verified invoices there are.
_MOST_SEEN = (
    "SELECT g, count(*) AS n,"
    " (SELECT count(*) FROM invoices WHERE customer_id = :c AND label = 'verified') AS total"
    " FROM (SELECT seller_gstin AS g, id FROM invoices WHERE customer_id = :c AND label = 'verified'"
    " UNION SELECT buyer_gstin, id FROM invoices WHERE customer_id = :c AND label = 'verified') s"
    " WHERE g IS NOT NULL GROUP BY g ORDER BY n DESC, g LIMIT 2"
)
_CHILD_NAMED = (
    "SELECT 1 FROM folders WHERE customer_id = :c AND parent_id = :p AND lower(name) = lower(:name)"
)


def file_document(conn: Connection, customer_id: str, document_id: str) -> str | None:
    """File the family of this just-read document by its type (R1 to R18). Returns the
    code of what was decided, for the log (filed, other, clash, no_owner, trash, off,
    inactive, not_current, no_type), or None when there was nothing to do. The audit rows
    (no actor: Kaagaz did it) commit with the change. A few indexed statements:
    O(v log D + log F) for the family's v versions and the customer's F folders."""
    params = {"c": customer_id, "d": document_id}
    if conn.execute(text(_PRE_CHECK), params).scalar() != PENDING:
        return None  # R5: filed, skipped or nothing to do; never twice
    _lock_tree(conn, customer_id)
    try:
        if lock_live_family(conn, customer_id, document_id) is None:
            return None  # deleted for good in the moment since
        in_trash = False
    except InTrash:  # the family lock is held: lock_live_family checks under it
        in_trash = True
    state = conn.execute(text(_STATE), params).mappings().one()
    if state["autofile"] != PENDING:
        return None  # a person moved it while it was read (R3)
    family = str(state["family_id"])
    if in_trash or state["org_status"] != "active" or not _filing_on(conn, customer_id, state):  # R14
        _set(conn, customer_id, family, None)
        return "trash" if in_trash else "inactive" if state["org_status"] != "active" else "off"
    if state["status"] != "ready" or not state["is_current"]:
        return "not_current"  # R15: the current version's run files the family
    if state["doc_type"] is None:
        return "no_type"  # R6, R18: stays pending, so a later read again can file it
    name = FOLDERS.get(state["doc_type"])
    if name is None:
        _set(conn, customer_id, family, SKIPPED)
        return "other"
    folder = _folder(conn, customer_id, f"type:{state['doc_type']}", name, state["uploaded_by"])
    if folder in ("clash", "no_owner"):
        _set(conn, customer_id, family, SKIPPED)
        return folder
    if state["doc_type"] == "invoice":
        folder = _party_folder(conn, customer_id, document_id, folder, state["uploaded_by"])
    conn.execute(text(_FILE_FAMILY), {"c": customer_id, "f": family, "folder": folder, "state": FILED})
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="document.autofile",
        target_type="document",
        target_id=document_id,
        details={"folder_id": folder},
    )
    return FILED


def _filing_on(conn: Connection, customer_id: str, state: RowMapping) -> bool:
    """Auto-filing is on for the uploader (AI settings, 0038): the grant and the platform
    switch, the organisation's default or lock and the uploader's own choice; with none
    saved, the admin's switch (``autofile_on``), as before. Two statements."""
    granted = bool(state["autofile_granted"]) and ai_settings.switch_on(conn, "autofile", customer_id)
    uploader = None if state["uploaded_by"] is None else str(state["uploaded_by"])
    return ai_settings.task_on(conn, customer_id, uploader, "autofile", granted, bool(state["autofile_on"]))


def _folder(conn: Connection, customer_id: str, key: str, name: str, owner: object) -> str:
    """The folder for this type (R9, R10, R12, R13): its id, or ``clash`` (a top-level
    folder of that name already files another type) or ``no_owner`` (none to use, and
    no uploader to own a new one)."""
    params = {"c": customer_id, "k": key, "name": name}
    keyed = conn.execute(text(_BY_KEY), params).scalar()
    if keyed is not None:
        return str(keyed)
    same = conn.execute(text(_BY_NAME), params).first()
    if same is not None:
        if same[1] is not None:
            return "clash"
        conn.execute(text(_ADOPT), {**params, "f": same[0]})
        return str(same[0])
    if owner is None:
        return "no_owner"
    return _create(conn, customer_id, None, name, owner, key)


def _create(
    conn: Connection, customer_id: str, parent: str | None, name: str, owner: object, key: str
) -> str:
    """A new folder Kaagaz made, with its key, audited (no actor)."""
    params = {"c": customer_id, "p": parent, "name": name, "u": owner, "k": key}
    made = str(conn.execute(text(_CREATE), params).scalar_one())
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="folder.create",
        target_type="folder",
        target_id=made,
        details={"parent_id": parent, "auto": True},
    )
    return made


def own_gstin(top: Sequence[tuple[str, int, int]]) -> str | None:
    """The organisation's own GSTIN from ``_MOST_SEEN``'s rows (GSTIN, invoices it is on,
    verified invoices): the top one when it is on at least 3 invoices, on more than half
    of them and on more than the next one, and checksum-valid; else None (not clear).
    O(1).

    ponytail: a guess from counts, upgrade path is a per-organisation GSTIN setting. An
    organisation with two GSTINs (two states) gets party folders for its main one only."""
    if not top:
        return None
    gstin, seen, total = top[0]
    if seen < OWN_MIN_INVOICES or seen * 2 <= total or (len(top) > 1 and top[1][1] >= seen):
        return None
    return gstin if gstin_valid(gstin) else None


def party(
    seller_name: str | None, seller: str | None, buyer_name: str | None, buyer: str | None, own: str | None
) -> tuple[str, str] | None:
    """The other party of an invoice, (GSTIN, folder name), or None. Exactly one side must
    be the organisation's own GSTIN, and the other side a different, checksum-valid one;
    the role does not matter, so a swapped seller and buyer give the same party. O(n)."""
    if own is None or (seller == own) == (buyer == own):
        return None  # not clear whose invoice it is, or both sides the same
    gstin, name = (buyer, buyer_name) if seller == own else (seller, seller_name)
    if gstin is None or not gstin_valid(gstin):
        return None
    return gstin, party_name(name, gstin)


def party_name(name: str | None, gstin: str) -> str:
    """The folder name for a party: control and format characters (bidi marks, zero-width
    spaces) removed, spaces collapsed, at most 80 characters; "GSTIN <gstin>" when that
    leaves nothing. O(n)."""
    kept = "".join(ch for ch in name or "" if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(kept.split())[:PARTY_NAME_MAX].rstrip() or f"GSTIN {gstin}"


def _party_folder(
    conn: Connection, customer_id: str, document_id: str, type_folder: str, owner: object
) -> str:
    """The party folder for a verified invoice (28b), or ``type_folder`` when there is none
    to use: no verified invoice, no clear party, no room or no owner, or both names taken.
    The caller holds the tree lock. O(I) for the customer's I invoices (the count)."""
    params = {"c": customer_id, "d": document_id}
    invoice = conn.execute(text(_INVOICE), params).first()
    if invoice is None:
        return type_folder
    top = [(str(r[0]), int(r[1]), int(r[2])) for r in conn.execute(text(_MOST_SEEN), params)]
    found = party(invoice[0], invoice[1], invoice[2], invoice[3], own_gstin(top))
    if found is None:
        return type_folder
    gstin, name = found
    key = f"party:{gstin}"
    keyed = conn.execute(text(_BY_KEY), {"c": customer_id, "k": key}).scalar()
    if keyed is not None:
        return str(keyed)
    depth: int | None = conn.execute(text(_DEPTH), {"c": customer_id, "f": type_folder}).scalar_one()
    if owner is None or depth is None or depth >= _MAX_DEPTH:
        return type_folder
    for candidate in (name, f"{name} ({gstin})"):
        taken = conn.execute(text(_CHILD_NAMED), {"c": customer_id, "p": type_folder, "name": candidate})
        if taken.first() is None:
            return _create(conn, customer_id, type_folder, candidate, owner, key)
    return type_folder


def _set(conn: Connection, customer_id: str, family_id: str, state: str | None) -> None:
    conn.execute(text(_SET_STATE), {"c": customer_id, "f": family_id, "state": state})


def undo(conn: Connection, customer_id: str, family_id: str) -> str | None:
    """Move a family Kaagaz filed back to the top level (R16), keeping the folder. Returns
    the folder it was in; None when it is not filed now (a person moved it since, or its
    folder was deleted), and nothing changes. The caller holds the family lock
    (``trash.lock_live_family``). O(v log D)."""
    row = conn.execute(text(_FILED_IN), {"c": customer_id, "f": family_id}).first()
    if row is None or row[0] != FILED or row[1] is None:
        return None
    conn.execute(text(_FILE_FAMILY), {"c": customer_id, "f": family_id, "folder": None, "state": None})
    return str(row[1])
