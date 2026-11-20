"""Auto-filing (feature 28, council 15 Oct 2026): a new upload nobody put in a folder
goes into its mapped department, then the folder for its type, once it is read.

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
the locks it reuses a matching department (never makes one, PRD 9 F-02), then reuses the type
folder wherever it now is, adopts an unkeyed same-name child, or creates it under the
department. Nothing is inserted to be caught: the unique index folders_autofile_key is only
the backstop. No model is called: department and type come from the document card's
``documents.doc_type``.

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
" (<GSTIN>)" added, and when that is taken too, or there is no room (depth 100) or nobody to
own a new folder, the type folder is used. Undo still moves the family to the top level.

``undo`` moves a filed family back to the top level, or to its department when it was filed
inside one; the folder it leaves goes when that leaves it empty (``folders.prune_empty``).

The department template (File management F3, 0041): a file is routed to a department from
its recognized type (finance documents to Accounts and HR, land records and contracts to
Land & Legal, forms and registers to Operations, letters to Correspondence). A mapped
department is used only when it exists (PRD 9 F-02, D2: admins make departments, Kaagaz never
does); with none, the file stays where it is, Not sorted. An upload already placed in a department keeps that
choice. The file is filed inside it: Category (the type folder,
key ``type:<type>:<department id>``), then Property for land records and deeds with a
survey number (``property:<village>:<number>``, "Survey 44, Wakad"; the village is a key
fact whose field names a village, in Latin letters, else "Survey 44"; a deed with no village
joins the one property folder of its number when there is just one), Party for verified
invoices (``party:<GSTIN>``, as above) or Year (``year:<yyyy>:<category id>``). A land record
with no survey number is filed by year. Folders Kaagaz makes carry the matching kind. An
unrecognized type remains unfiled; it never creates a guessed department. A Property or
Party folder already holding its key is looked up first and used wherever it now is, so no
department or category is made that the file would not go into (issue 414).
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz.chunking.script import fold_digits, is_devanagari, latin_spelling, sound_key
from kaagaz.db import ai_settings, audit
from kaagaz.db.folders import (
    _DEPTH,
    MAX_DEPTH,
    MoveChangesAccess,
    Rights,
    _lock_tree,
    changes_access,
    place_of,
    prune_empty,
)
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

# A document's recognized type is enough to choose these departments without another model call.
DEPARTMENTS = {
    "invoice": "Accounts and HR",
    "bank_statement": "Accounts and HR",
    "receipt": "Accounts and HR",
    "land_record": "Land & Legal",
    "contract": "Land & Legal",
    "register": "Operations",
    "form": "Operations",
    "letter": "Correspondence",
}
DEPARTMENT_ALIASES = {
    "Accounts and HR": ("Accounts and HR", "Accounts", "Finance", "Accounts and Finance"),
    "Land & Legal": ("Land & Legal", "Land and Legal", "Legal", "Land"),
    "Operations": ("Operations", "Administration", "Admin"),
    "Correspondence": ("Correspondence", "Communications"),
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
    " c.status AS org_status, c.autofile_on, c.autofile_granted,"
    " d.folder_id, fo.kind AS folder_kind, d.card, d.document_date, d.created_at, d.held_for"
    " FROM documents d"
    " JOIN documents r ON r.customer_id = d.customer_id AND r.id = d.family_id"
    " JOIN customers c ON c.id = d.customer_id"
    " LEFT JOIN folders fo ON fo.customer_id = d.customer_id AND fo.id = d.folder_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)
_SET_STATE = "UPDATE documents SET autofile = :state WHERE customer_id = :c AND id = :f"
_BY_KEY = "SELECT id FROM folders WHERE customer_id = :c AND autofile_key = :k"
_ADOPT = "UPDATE folders SET autofile_key = :k WHERE customer_id = :c AND id = :f"
_CREATE = (
    "INSERT INTO folders (customer_id, parent_id, name, created_by, autofile_key, kind)"
    " VALUES (:c, :p, :name, :u, :k, :kind) RETURNING id"
)
# The organisation's switch, or its grant and an "on" level for it or the uploader (:by), on
# ``customers``: the insert's rule for a new upload (``PgDocuments.add_if_absent``) and
# ``applies``', so the two can't differ.
APPLIES = (
    "(autofile_on OR (autofile_granted AND EXISTS (SELECT 1 FROM ai_task_settings s"
    " WHERE s.customer_id = :c AND s.task = 'autofile' AND s.level = 'on'"
    " AND (s.user_id IS NULL OR s.user_id = :by))))"
)
# PRD 9 F-02 (D2): ``departments``, whether there is a department to sort into (Kaagaz makes none)
# that the person sees (visible_folders: only the API asks this, never the worker).
_APPLIES = (
    "SELECT " + APPLIES + " AS pending,"  # noqa: S608 - a fixed fragment, APPLIES
    " autofile_on, autofile_granted, CAST(:by AS uuid) AS uploaded_by,"
    " EXISTS (SELECT 1 FROM visible_folders f WHERE f.customer_id = :c AND f.parent_id IS NULL"
    " AND f.kind = 'department') AS departments FROM customers WHERE id = :c"
)
_DEPARTMENTS = (
    "SELECT id, name FROM folders"
    " WHERE customer_id = :c AND parent_id IS NULL AND kind = 'department' ORDER BY lower(name), id"
)
# File management F3: the property folders of one survey number, whatever their village (at
# most 2 read: one is used only when it is the only one).
_SAME_NUMBER = (
    "SELECT autofile_key FROM folders WHERE customer_id = :c AND autofile_key LIKE :k ORDER BY id LIMIT 2"
)
# File management F3: a folder of this name inside the parent (at most one: folders_sibling_name).
_CHILD = (
    "SELECT id, autofile_key FROM folders"
    " WHERE customer_id = :c AND parent_id = :p AND lower(name) = lower(:name)"
)
# The family into a folder (filed) or back to the top level (undone, held again for :held), its
# state with it.
_FILE_FAMILY = (
    "UPDATE documents SET folder_id = :folder, held_for = CAST(:held AS uuid),"
    " autofile = CASE WHEN id = family_id THEN CAST(:state AS text) END"
    " WHERE customer_id = :c AND family_id = :f"
)
# PRD 7 (#601): when it filed the file where its uploader can't see it, the file given to them.
_GIVE_UPLOADER = "SELECT kaagaz_give_uploader(:d)"
# PRD 7 decision 10 (#601; #594's note on this Undo): whom the family was held for when it was
# filed (its filing's audit row says), so its Undo holds it for them again.
_HELD_BEFORE = (
    "SELECT details ->> 'held_for' FROM audit_log WHERE customer_id = :c AND family_id = :f"
    " AND action = 'document.autofile' ORDER BY id DESC LIMIT 1"
)
# An Undo back to no folder of a file that was open there holds it for its uploader once the
# organisation keeps something to some people (fail closed).
_HOLD_FOR = (
    "SELECT r.uploaded_by FROM documents r JOIN customers c ON c.id = r.customer_id"
    " WHERE r.customer_id = :c AND r.id = :f AND c.walls_set"
)
_FILED_IN = (
    "SELECT autofile, folder_id, department_id, private_folder_id FROM documents"
    " WHERE customer_id = :c AND id = :f"
)

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
    code of what was decided, for the log (filed, other, no_department, trash, off,
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
    # Issue 414: the Property or Party folder is used wherever it now is, so it is looked up
    # first; a department or category is made only when the file goes into it.
    own = _own_folder(conn, customer_id, document_id, state)
    found = None if own is None else conn.execute(text(_BY_KEY), {"c": customer_id, "k": own[1]}).scalar()
    if found is not None:
        folder = str(found)
    else:
        department = (
            str(state["folder_id"])
            if state["folder_kind"] == "department"
            else _department(conn, customer_id, state["doc_type"])
        )
        if department is None:
            _set(conn, customer_id, family, SKIPPED)
            return "no_department"
        folder = _in_department(conn, customer_id, department, state, own)
    filing = {"c": customer_id, "f": family, "folder": folder, "state": FILED, "held": None}
    conn.execute(text(_FILE_FAMILY), filing)
    given = conn.execute(text(_GIVE_UPLOADER), {"d": document_id}).scalar_one()
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="document.autofile",
        target_type="document",
        target_id=document_id,
        details={
            "folder_id": folder,
            **({"given": given} if given else {}),
            **({"held_for": str(state["held_for"])} if state["held_for"] is not None else {}),
        },
    )
    return FILED


def applies(conn: Connection, customer_id: str, user_id: str) -> bool:
    """Would this user's upload with no folder be filed by its type ("Let Kaagaz sort them",
    #484)? The insert's rule marks it pending and ``_filing_on`` files it. PRD 9 F-02: never while
    the organisation has no department, as Kaagaz makes none. Three statements."""
    row = conn.execute(text(_APPLIES), {"c": customer_id, "by": user_id}).mappings().one()
    return bool(row["pending"]) and bool(row["departments"]) and _filing_on(conn, customer_id, row)


def _filing_on(conn: Connection, customer_id: str, state: RowMapping) -> bool:
    """Auto-filing is on for the uploader (AI settings, 0038): the grant and the platform
    switch, the organisation's default or lock and the uploader's own choice; with none
    saved, the admin's switch (``autofile_on``), as before. Two statements."""
    granted = bool(state["autofile_granted"]) and ai_settings.switch_on(conn, "autofile", customer_id)
    uploader = None if state["uploaded_by"] is None else str(state["uploaded_by"])
    return ai_settings.task_on(conn, customer_id, uploader, "autofile", granted, bool(state["autofile_on"]))


# --- the department template (File management F3) ---
_SURVEY = re.compile(r"[0-9]+(?:/[0-9]+)*")
VILLAGE_MAX = 40


def _department(conn: Connection, customer_id: str, doc_type: str) -> str | None:
    """The existing department mapped to this recognized type, else None; the caller holds the
    tree lock. PRD 9 F-02 (D2): Kaagaz never makes a department."""
    name = DEPARTMENTS.get(doc_type)
    if name is None:
        return None
    aliases = {alias.casefold() for alias in DEPARTMENT_ALIASES[name]}
    rows = conn.execute(text(_DEPARTMENTS), {"c": customer_id}).all()
    matching = next((row for row in rows if row[1].casefold() in aliases), None)
    return None if matching is None else str(matching[0])


def survey_digits(value: str) -> str | None:
    """A survey or gat number as digits and slashes, spaces removed and Devanagari digits as
    ASCII ("स.नं. ४४/२" is "44/2", never "44"); None with no digit. The list filter's SQL
    (``listing._WITH_PROPERTY``) reads a card the same way. O(n)."""
    found = _SURVEY.search("".join(fold_digits(value).split()))
    return None if found is None else found[0][:32]


def survey_number(card: Mapping[str, Any] | None) -> str | None:
    """The card's first survey number (``survey_digits``); None when there is none. O(n)."""
    for identifier in (card or {}).get("identifiers") or ():
        if identifier.get("kind") == "survey_number":
            number = survey_digits(str(identifier.get("value") or ""))
            if number is not None:
                return number
    return None


def village(card: Mapping[str, Any] | None) -> str | None:
    """The village as the card has it: a key fact whose field names a village (the card has
    no village field of its own), in Latin letters; None when there is none. O(n)."""
    for fact in (card or {}).get("key_facts") or ():
        value = " ".join(str(fact.get("value") or "").split())
        if "village" in str(fact.get("field") or "") and value:
            latin = latin_spelling(value) if is_devanagari(value) else value
            return latin.title()[:VILLAGE_MAX].strip() or None
    return None


def property_folder(number: str, place: str | None) -> tuple[str, str]:
    """The property folder's key and name: ``property:vkd:44`` and "Survey 44, Wakad", or
    ``property::44`` and "Survey 44" with no village. The key's village is each word's sound
    key (``script.sound_key``: v/w, aspirates, doubled letters and inner "a" folded), so
    "Wakad" and वाकड's "Vakad" share one folder, named as the first one made. O(n)."""
    slug = "-".join(filter(None, map(sound_key, re.split(r"[^a-z]+", (place or "").lower()))))
    slug = slug[:VILLAGE_MAX].strip("-")
    name = f"Survey {number}" if place is None else f"Survey {number}, {place}"
    return f"property:{slug}:{number}", name


def _own_folder(
    conn: Connection, customer_id: str, document_id: str, state: RowMapping
) -> tuple[str, str, str] | None:
    """The file's own folder below its category, as (kind, key, name): Property for a land
    record or a deed with a survey number, Party for a verified invoice with one clear other
    party (28b); None for any other file. The caller holds the tree lock. A few indexed
    statements, O(I) for an invoice (the count of the customer's I invoices)."""
    card = state["card"]
    number = survey_number(card)
    if number is not None and state["doc_type"] in ("land_record", "contract"):
        place = village(card)
        key, name = property_folder(number, place)
        if place is None:  # a deed with no village joins the one property of that number
            same = conn.execute(text(_SAME_NUMBER), {"c": customer_id, "k": f"property:%:{number}"}).all()
            key = str(same[0][0]) if len(same) == 1 else key
        return "property", key, name
    if state["doc_type"] != "invoice":
        return None
    params = {"c": customer_id, "d": document_id}
    invoice = conn.execute(text(_INVOICE), params).first()
    if invoice is None:
        return None
    top = [(str(r[0]), int(r[1]), int(r[2])) for r in conn.execute(text(_MOST_SEEN), params)]
    found = party(invoice[0], invoice[1], invoice[2], invoice[3], own_gstin(top))
    return None if found is None else ("party", f"party:{found[0]}", found[1])


def _in_department(
    conn: Connection, customer_id: str, department: str, state: RowMapping, own: tuple[str, str, str] | None
) -> str:
    """The folder for a file uploaded into a department: Department, then Category (the
    type folder), then its own folder (``own``: Property or Party, none of its key yet) or
    Year (the document date, else the upload's). A level that can't be made (no owner, no
    room, a name taken) leaves the file one level up. The caller holds the tree lock. A few
    indexed statements."""
    owner = state["uploaded_by"]
    # A deed with a survey number goes next to its land.
    kind = "land_record" if own is not None and own[0] == "property" else state["doc_type"]
    category = _child(
        conn, customer_id, department, f"type:{kind}:{department}", FOLDERS[kind], "category", owner
    )
    if category == department:
        return department
    if own is not None and own[0] == "property":
        return _child(conn, customer_id, category, own[1], own[2], "property", owner)
    if own is not None:
        return _party_folder(conn, customer_id, category, own, owner)
    if kind == "invoice":
        return category  # no verified invoice or no clear other party: no Party folder
    year = filing_year(state["document_date"], state["created_at"])
    return _child(conn, customer_id, category, f"year:{year}:{category}", year, "year", owner)


def filing_year(document_date: date | None, created_at: datetime) -> str:
    """The Year folder's name: the document date's year when it is 1900 to next year, else
    the upload's (a misread "24" or "0024" is not a year), as 4 digits. O(1)."""
    y = document_date.year if document_date is not None else created_at.year
    if not 1900 <= y <= datetime.now(UTC).year + 1:
        y = created_at.year
    return f"{y:04d}"


def _child(
    conn: Connection, customer_id: str, parent: str, key: str, name: str, kind: str, owner: object
) -> str:
    """The folder holding ``key`` wherever it now is; else ``parent``'s child of that name
    with no key, adopted; else a new child. ``parent`` itself when the name is taken by a
    keyed folder, there is no room (depth 100) or no owner."""
    params = {"c": customer_id, "k": key, "p": parent, "name": name}
    keyed = conn.execute(text(_BY_KEY), params).scalar()
    if keyed is not None:
        return str(keyed)
    same = conn.execute(text(_CHILD), params).first()
    if same is not None:
        if same[1] is not None:
            return parent
        conn.execute(text(_ADOPT), {**params, "f": same[0]})
        return str(same[0])
    depth: int | None = conn.execute(text(_DEPTH), {"c": customer_id, "f": parent}).scalar_one()
    if owner is None or depth is None or depth >= MAX_DEPTH:
        return parent
    return _create(conn, customer_id, parent, name, owner, key, kind)


def _create(
    conn: Connection,
    customer_id: str,
    parent: str | None,
    name: str,
    owner: object,
    key: str | None,
    kind: str,
) -> str:
    """A new folder Kaagaz made, with its key and kind, audited (no actor)."""
    params = {"c": customer_id, "p": parent, "name": name, "u": owner, "k": key, "kind": kind}
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
    conn: Connection, customer_id: str, type_folder: str, own: tuple[str, str, str], owner: object
) -> str:
    """A new party folder for a verified invoice (28b; ``own`` from ``_own_folder``, its key in
    no folder yet), or ``type_folder`` when none can be made: no room or no owner, or both
    names taken. The caller holds the tree lock. A few indexed statements."""
    _, key, name = own
    gstin = key.removeprefix("party:")
    depth: int | None = conn.execute(text(_DEPTH), {"c": customer_id, "f": type_folder}).scalar_one()
    if owner is None or depth is None or depth >= MAX_DEPTH:
        return type_folder
    for candidate in (name, f"{name} ({gstin})"):
        taken = conn.execute(text(_CHILD_NAMED), {"c": customer_id, "p": type_folder, "name": candidate})
        if taken.first() is None:
            return _create(conn, customer_id, type_folder, candidate, owner, key, "party")
    return type_folder


def _set(conn: Connection, customer_id: str, family_id: str, state: str | None) -> None:
    conn.execute(text(_SET_STATE), {"c": customer_id, "f": family_id, "state": state})


class HeldForAnother(Exception):
    """PRD 7 (#601): the Undo would hold the file for its uploader, and the person undoing it is
    neither them nor Management (as #594's Q4 for a version's Undo)."""


def undo(
    conn: Connection,
    customer_id: str,
    family_id: str,
    *,
    by: str | None = None,
    management: bool = False,
    rights: Rights | None = None,
) -> str | None:
    """Move a family Kaagaz filed back where it was before filing (R16): its department when
    it was filed inside one (F3), else the top level. PRD 7 decision 10 (#601): a family held when
    it was filed is held again for the same person, and one open in no folder is held for its
    uploader once the organisation keeps something to some people (HeldForAnother when ``by`` is
    neither the holder nor ``management``); one not held is a move, under ``rights``, the person's
    (MoveChangesAccess, PRD 7 decision 18). The folder it leaves, and its parents,
    go when that leaves them empty (``folders.prune_empty``). Returns the folder it was in;
    None when it is not filed now (a person moved it since, or its folder was deleted), and
    nothing changes. The caller holds the tree lock and the family lock
    (``trash.lock_live_family``). O(v log D + depth)."""
    row = conn.execute(text(_FILED_IN), {"c": customer_id, "f": family_id}).first()
    if row is None or row[0] != FILED or row[1] is None:
        return None
    params = {"c": customer_id, "f": family_id, "folder": row[1]}
    held = conn.execute(text(_HELD_BEFORE), params).scalar()
    # Undo's way back (File management F3): the department above the filed folder, where a file
    # uploaded into a department was before filing; NULL (the top level) otherwise. PRD 7 (#606):
    # the file's stored department (0085), the same id, never a walk up the tree, which the database
    # rule stops below a department the person can't see.
    # ponytail: a type folder a person moved into a department sends Undo there; remember the
    # upload's folder if that matters.
    back = None if held is not None else row[2]
    if held is None and back is None:
        held = conn.execute(text(_HOLD_FOR), params).scalar()
    if held is not None and str(held) != by and not management:
        raise HeldForAnother()
    if held is None and rights is not None:
        before = tuple(None if v is None else str(v) for v in (row[2], row[3]))
        after = place_of(conn, customer_id, None if back is None else str(back))
        if changes_access(conn, customer_id, (before[0], before[1]), after) and not rights.allow(
            before[0], after[0]
        ):
            raise MoveChangesAccess()
    conn.execute(text(_FILE_FAMILY), {**params, "folder": back, "state": None, "held": held})
    prune_empty(conn, customer_id, [str(row[1])])
    return str(row[1])
