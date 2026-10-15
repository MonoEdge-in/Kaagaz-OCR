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

``undo`` moves a filed family back to the top level; the folder stays.
"""

from sqlalchemy import Connection, text

from kaagaz.db import audit
from kaagaz.db.folders import _lock_tree
from kaagaz.db.trash import InTrash, lock_live_family

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
    " c.status AS org_status, c.autofile_on FROM documents d"
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
    " VALUES (:c, NULL, :name, :u, :k) RETURNING id"
)
# The family into a folder (filed) or back to the top level (undone), its state with it.
_FILE_FAMILY = (
    "UPDATE documents SET folder_id = :folder,"
    " autofile = CASE WHEN id = family_id THEN CAST(:state AS text) END"
    " WHERE customer_id = :c AND family_id = :f"
)
_FILED_IN = "SELECT autofile, folder_id FROM documents WHERE customer_id = :c AND id = :f"


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
    if in_trash or state["org_status"] != "active" or not state["autofile_on"]:  # R14: quietly not filed
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
    made = str(conn.execute(text(_CREATE), {**params, "u": owner}).scalar_one())
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="folder.create",
        target_type="folder",
        target_id=made,
        details={"parent_id": None, "auto": True},
    )
    return made


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
