"""Party folders (backlog 28b, final polish 16 Oct 2026) against PostgreSQL: a verified
invoice goes into "Invoices / <the other party>", keyed by the party's GSTIN; the other
party is the same whichever role it was read in; nothing deeper when the organisation's own
GSTIN is not clear or the invoice needs review; a name a person already uses gets the GSTIN
added, and a person's folder is never taken; Undo moves the file to the top level. The pure
rules: test_autofile_party.py; across customers: test_isolation_autofile.py.
"""

import pytest
from sqlalchemy import text

from kaagaz.db import autofile
from kaagaz.db.engine import Database
from kaagaz.db.folders import create_folder, move_folder, rename_folder
from kaagaz.db.trash import lock_live_family
from tests.db_support import insert_document, insert_folder
from tests.test_autofile_db import audit, folders, organisation, read, state, upload
from tests.test_autofile_party import MEHTA, OWN, SHARMA

pytestmark = pytest.mark.db

_INVOICE = (
    "INSERT INTO invoices (customer_id, document_id, seller_name, seller_gstin, buyer_name, buyer_gstin,"
    " label, check_status) VALUES (:c, :d, :sn, :s, :bn, :b, :label, 'not_checked')"
)
_PATH = (
    "SELECT p.name, f.name FROM documents d JOIN folders f ON f.customer_id = d.customer_id"
    " AND f.id = d.folder_id LEFT JOIN folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)


def invoice(
    db: Database,
    customer: str,
    document: str,
    seller: tuple[str, str | None],
    buyer: tuple[str, str | None],
    label: str = "verified",
) -> None:
    """The invoice row the reading saved: (name, GSTIN) of each side."""
    params = {"c": customer, "d": document, "sn": seller[0], "s": seller[1], "bn": buyer[0], "b": buyer[1]}
    with db.customer(customer) as conn:
        conn.execute(text(_INVOICE), {**params, "label": label})


def history(db: Database, customer: str, count: int = 3) -> None:
    """Earlier verified sales bills, so the organisation's own GSTIN is clear."""
    for n in range(count):
        with db.customer(customer) as conn:
            document = insert_document(conn, customer)
        invoice(db, customer, document, ("Our Shop", OWN), (f"Buyer {n}", None))


def bill(
    db: Database, customer: str, admin: str, seller: tuple[str, str | None], label: str = "verified"
) -> str:
    """A new upload of a bill from ``seller`` to the organisation, read and filed."""
    document = upload(db, customer, admin)
    invoice(db, customer, document, seller, ("Our Shop", OWN), label)
    read(db, customer, document)
    return document


def path(db: Database, customer: str, document: str) -> tuple[str | None, str]:
    with db.customer(customer) as conn:
        row = conn.execute(text(_PATH), {"c": customer, "d": document}).one()
    return row[0], row[1]


def test_a_verified_bill_goes_into_a_folder_for_its_party_inside_invoices(db: Database) -> None:
    customer, admin = organisation(db)
    history(db, customer)
    document = bill(db, customer, admin, ("Mehta  Textiles\u200b", MEHTA))

    assert state(db, customer, document)[0] == "filed"
    assert path(db, customer, document) == ("Invoices", "Mehta Textiles")
    by_name = {f[1]: f for f in folders(db, customer)}
    department, invoices, mehta = by_name["Accounts and HR"], by_name["Invoices"], by_name["Mehta Textiles"]
    assert (mehta[2], mehta[3], mehta[4]) == (invoices[0], f"party:{MEHTA}", admin)
    assert audit(db, customer) == [
        ("folder.create", None, {"parent_id": None, "auto": True}),
        ("folder.create", None, {"parent_id": department[0], "auto": True}),
        ("folder.create", None, {"parent_id": invoices[0], "auto": True}),
        ("document.autofile", None, {"folder_id": mehta[0]}),
    ]


def test_one_party_is_one_folder_whatever_name_or_role_it_was_read_with(db: Database) -> None:
    customer, admin = organisation(db)
    history(db, customer)
    first = bill(db, customer, admin, ("Mehta Textiles", MEHTA))
    renamed = bill(db, customer, admin, ("MEHTA TEXTILES PVT LTD", MEHTA))  # grouped by GSTIN, not name
    sold = upload(db, customer, admin)  # a sale to Mehta, or the roles read the wrong way round
    invoice(db, customer, sold, ("Our Shop", OWN), ("Mehta Textiles", MEHTA))
    read(db, customer, sold)

    assert {path(db, customer, d) for d in (first, renamed, sold)} == {("Invoices", "Mehta Textiles")}
    assert sorted(f[1] for f in folders(db, customer)) == ["Accounts and HR", "Invoices", "Mehta Textiles"]


def test_a_folder_named_after_the_organisation_itself_is_never_made(db: Database) -> None:
    """M4: its own sales bills don't pile into a folder named after itself, and with no
    clear own GSTIN yet, nothing goes deeper than Invoices."""
    customer, admin = organisation(db)
    early = bill(db, customer, admin, ("Mehta Textiles", MEHTA))  # no history: own GSTIN not clear
    # Changed on purpose (#239, 27 Oct 2026): Invoices sits in the department its type maps to
    # (24cea75), so its parent is that department; nothing goes deeper than Invoices.
    assert path(db, customer, early) == ("Accounts and HR", "Invoices")
    history(db, customer)
    both = upload(db, customer, admin)
    invoice(db, customer, both, ("Our Shop", OWN), ("Our Shop", OWN))
    read(db, customer, both)
    assert path(db, customer, both) == ("Accounts and HR", "Invoices")
    assert [f[1] for f in folders(db, customer)] == ["Accounts and HR", "Invoices"]


def test_a_bill_that_needs_review_stays_in_invoices(db: Database) -> None:
    customer, admin = organisation(db)
    history(db, customer)
    document = bill(db, customer, admin, ("Mehta Textiles", MEHTA), label="needs_review")
    # Changed on purpose (#239, 27 Oct 2026): Invoices sits in the department its type maps to
    # (24cea75), so its parent is that department; nothing goes deeper than Invoices.
    assert path(db, customer, document) == ("Accounts and HR", "Invoices")


def test_a_persons_folder_of_that_name_is_never_taken_the_gstin_is_added(db: Database) -> None:
    customer, admin = organisation(db)
    history(db, customer)
    with db.customer(customer) as conn:
        department = create_folder(
            conn, customer, name="Finance", parent_id=None, created_by=admin, kind="department"
        )
        invoices = insert_folder(conn, customer, admin, "Invoices", parent_id=department.id)
        mine = insert_folder(conn, customer, admin, "mehta textiles", invoices)
    document = bill(db, customer, admin, ("Mehta Textiles", MEHTA))
    assert path(db, customer, document) == ("Invoices", f"Mehta Textiles ({MEHTA})")
    assert next(f for f in folders(db, customer) if f[0] == mine)[3] is None  # still the person's

    with db.customer(customer) as conn:
        insert_folder(conn, customer, admin, "Sharma", invoices)
        insert_folder(conn, customer, admin, f"Sharma ({SHARMA})", invoices)
    both_taken = bill(db, customer, admin, ("Sharma", SHARMA))
    # Changed on purpose (#239, 27 Oct 2026): this Invoices is inside the Finance department (24cea75).
    assert path(db, customer, both_taken) == ("Finance", "Invoices")


def test_a_party_folder_found_elsewhere_leaves_no_empty_department_or_invoices(db: Database) -> None:
    """Issue 414: the party's folder is used wherever it is, so nothing is made above where it
    would have gone: before, a new Invoices (and department) was made first and left empty."""
    customer, admin = organisation(db)
    history(db, customer)
    first = bill(db, customer, admin, ("Mehta Textiles", MEHTA))
    with db.customer(customer) as conn:
        purchases = create_folder(
            conn, customer, name="Purchases", parent_id=None, created_by=admin, kind="department"
        )
    second = upload(db, customer, admin, folder=purchases.id)  # uploaded into another department
    invoice(db, customer, second, ("Mehta Textiles", MEHTA), ("Our Shop", OWN))
    read(db, customer, second)

    assert path(db, customer, second) == path(db, customer, first) == ("Invoices", "Mehta Textiles")
    assert sorted(f[1] for f in folders(db, customer)) == [
        "Accounts and HR",
        "Invoices",
        "Mehta Textiles",
        "Purchases",
    ]


def test_a_moved_and_renamed_party_folder_is_still_used_and_undo_goes_to_the_top_level(db: Database) -> None:
    customer, admin = organisation(db)
    history(db, customer)
    first = bill(db, customer, admin, ("Mehta Textiles", MEHTA))
    [mehta] = [f[0] for f in folders(db, customer) if f[3] == f"party:{MEHTA}"]
    with db.customer(customer) as conn:
        rename_folder(conn, customer, mehta, name="Mehta 2026")
        move_folder(conn, customer, mehta, parent_id=None)

    second = bill(db, customer, admin, ("Mehta Textiles", MEHTA))
    assert path(db, customer, second) == (None, "Mehta 2026")

    with db.customer(customer) as conn:
        assert lock_live_family(conn, customer, first) is not None
        assert autofile.undo(conn, customer, first) == mehta
    assert state(db, customer, first) == (None, None)
    # Changed on purpose (Vrushit UAT 3, 27 Oct 2026): moving Mehta out left Invoices and its
    # department empty, and Kaagaz made them, so they went; the party folder stays.
    assert [f[1] for f in folders(db, customer)] == ["Mehta 2026"]
