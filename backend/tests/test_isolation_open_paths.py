"""#594: no path leaves a file or folder open to everyone by accident (blocks PRD 7 PR 9).

PR 6's walled organisation (``test_isolation_access_views._walled``): Finance is Department only
(``member`` and ``head`` in it), "Salaries" inside it is private and names ``named``, Sales is open.

* A file restored after its folder was deleted goes into its private folder while that is live,
  else is held for the restorer (Q1); with no private folder, the top of its department while that
  is live, else held (Q2); in no department and no private folder, no folder as before.
* A held file linked as a new version and then undone is held again for the same person (Q4: an
  Undo that holds it for someone else is the holder's or Management's); one whose folder is gone
  goes no wider than while it was linked.
* A folder deleted with its contents kept is refused when what it holds would be seen by other
  people where it goes (Q5).
* A folder restored with its folder gone goes under the private folder or department it was in,
  and comes back private, with nobody named, when one of them is gone (Q6).

Each is checked through the access views (who sees the file), and check-access finds nothing."""

import threading
import uuid
from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.db import folders as folders_sql
from kaagaz.db import trash
from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_folder, new_customer
from tests.test_api_support import CSRF, Api, Signed
from tests.test_isolation_access_views import _DEPARTMENTS, _SUGGEST, Org, _app, _seen, _walled
from tests.test_isolation_pr7_review import _private
from tests.test_migration_0085_db import _differences
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def _department(api: Api, org: Org, name: str, *members: Signed) -> str:
    """A new open department with these people in it."""
    admin = org.people["admin"].user_id
    folder = api.add_folder(org.c, admin, name)
    _owner(api.db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": folder})
    for person in members:
        _owner(
            api.db, _DEPARTMENTS, {"c": org.c, "d": folder, "u": person.user_id, "head": False, "by": admin}
        )
    return folder


def _bin(signed: Signed, document: str) -> None:
    assert signed.client.delete(f"/api/documents/{document}", headers=CSRF).status_code == 204


def _gone(org: Org, folder: str) -> None:
    """An empty folder deleted (a file in Trash in it loses its folder link)."""
    reply = org.people["admin"].client.delete(f"/api/folders/{folder}", headers=CSRF)
    assert reply.status_code == 204, reply.text


def _back(signed: Signed, document: str) -> dict[str, Any]:
    reply = signed.client.post(f"/api/documents/{document}/restore", headers=CSRF)
    assert reply.status_code == 200, reply.text
    placed: dict[str, Any] = reply.json()["placed"]
    return placed


def _where(db: Database, document: str) -> tuple[str | None, str | None]:
    """(folder_id, held_for)."""
    row = _owner(db, "SELECT folder_id, held_for FROM documents WHERE id = :d", {"d": document})[0]
    return tuple(None if v is None else str(v) for v in row)  # type: ignore[return-value]


def _version(db: Database, org: Org) -> int:
    return int(_owner(db, "SELECT access_version FROM customers WHERE id = :c", {"c": org.c})[0][0])


def _sees(db: Database, org: Org, document: str, *names: str) -> set[str]:
    """Which of these people see the file (through PR 6's view, as the app role)."""
    return {name for name in names if document in _seen(_app(db), org.c, org.people[name].user_id)}


def _restored(api: Api, org: Org) -> list[dict[str, Any]]:
    return [row[3] for row in api.audit_rows(org.c) if row[0] == "document.restore"]


# --- path 1: a file restored after its folder went ----------------------------------------------


def test_a_file_whose_folder_went_comes_back_at_the_top_of_its_live_department(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    finance, member = org.folders["finance"], org.people["member"]
    budgets = api.add_folder(org.c, member.user_id, "Budgets", finance)
    plan = api.add_document(org.c, name="plan.pdf", folder_id=budgets, uploaded_by=member.user_id)
    _bin(member, plan)
    _gone(org, budgets)

    placed = _back(member, plan)

    assert (placed["held"], placed["folder"]["id"], placed["folder"]["name"]) == (False, finance, "Finance")
    assert _where(db, plan) == (finance, None)
    assert _sees(db, org, plan, "member", "head", "plain", "named") == {"member", "head"}
    assert _restored(api, org) == [{"to": finance}]
    assert _differences(db, org.c) == []


@pytest.mark.parametrize("department", ["deleted", "in Trash"])
def test_a_file_whose_department_went_too_is_held_for_the_restorer_even_when_it_was_open(
    db: Database, department: str
) -> None:
    """Q2: Legal was open. Its file is held: the restorer and Management see it, nobody else. A
    department in Trash is not live either."""
    api = Api(db)
    org = _walled(api)
    member = org.people["member"]
    legal = _department(api, org, "Legal", member)
    briefs = api.add_folder(org.c, member.user_id, "Briefs", legal)
    brief = api.add_document(org.c, name="brief.pdf", folder_id=briefs, uploaded_by=member.user_id)
    _bin(member, brief)
    _gone(org, briefs)
    if department == "deleted":
        _gone(org, legal)
    else:
        _trash_folder(org, legal)
    before = _version(db, org)

    assert _back(member, brief) == {"held": True, "folder": None}

    assert _where(db, brief) == (None, member.user_id)
    assert _version(db, org) > before
    everyone = ("member", "management", "head", "plain", "named", "admin")
    assert _sees(db, org, brief, *everyone) == {"member", "management"}
    assert _restored(api, org) == [{"held": True}]
    assert _differences(db, org.c) == []


def test_a_file_whose_private_folder_went_is_held_not_put_in_its_open_to_members_department(
    db: Database,
) -> None:
    """Q1: Vault (private, in Finance) is gone, Finance is live: the top of Finance would show it
    to every member, so it is held for the restorer (Finance's head, who saw it in Trash)."""
    api = Api(db)
    org = _walled(api)
    head, named = org.people["head"], org.people["named"]
    vault = api.add_folder(org.c, head.user_id, "Vault", org.folders["finance"])
    _private(db, org, vault, named.user_id)
    slip = api.add_document(org.c, name="slip.pdf", folder_id=vault, uploaded_by=head.user_id)
    _bin(head, slip)
    _gone(org, vault)

    assert _back(head, slip) == {"held": True, "folder": None}

    assert _where(db, slip) == (None, head.user_id)
    assert _sees(db, org, slip, "head", "member", "named", "plain", "management") == {"head", "management"}
    assert _differences(db, org.c) == []


def test_a_file_whose_folder_in_a_live_private_folder_went_comes_back_in_the_private_folder(
    db: Database,
) -> None:
    api = Api(db)
    org = _walled(api)
    named, salaries = org.people["named"], org.folders["salaries"]
    slips = api.add_folder(org.c, named.user_id, "Slips", salaries)
    slip = api.add_document(org.c, name="slip.pdf", folder_id=slips, uploaded_by=named.user_id)
    _bin(named, slip)
    _gone(org, slips)

    placed = _back(named, slip)

    assert (placed["held"], placed["folder"]["id"]) == (False, salaries)
    assert _where(db, slip) == (salaries, None)
    assert _sees(db, org, slip, "named", "head", "member", "plain") == {"named", "head"}
    assert _differences(db, org.c) == []


def test_a_file_in_no_department_or_in_a_live_folder_comes_back_as_before(db: Database) -> None:
    """Day one: in no department and no private folder it was open, and it is again, in no folder
    and held for no one; a file whose folder is live goes back into it."""
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    misc = api.add_folder(org.c, admin.user_id, "Misc")
    loose = api.add_document(org.c, name="misc.pdf", folder_id=misc, uploaded_by=admin.user_id)
    kept = api.add_document(org.c, name="kept.pdf", folder_id=org.folders["sales"], uploaded_by=admin.user_id)
    for document in (loose, kept):
        _bin(admin, document)
    _gone(org, misc)

    assert [_back(admin, document) for document in (loose, kept)] == [None, None]

    assert _where(db, loose) == (None, None)
    assert _where(db, kept) == (org.folders["sales"], None)
    assert _sees(db, org, loose, "plain", "member") == {"plain", "member"}
    assert _restored(api, org) == [{}, {}]
    assert _differences(db, org.c) == []


def test_a_file_held_before_it_went_to_trash_keeps_its_holder(db: Database) -> None:
    """The restorer is never written over a holder the file already has."""
    c, holder = new_customer(db)
    with db.customer(c) as conn:
        legal = insert_folder(conn, c, holder, "Legal")
        briefs = insert_folder(conn, c, holder, "Briefs", legal)
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": legal})
    with db.customer(c) as conn:
        document = insert_document(conn, c, folder_id=briefs)
    _owner(
        db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u, folder_id = NULL, held_for = :u"
        " WHERE id = :d",
        {"u": holder, "d": document},
    )
    _owner(db, "DELETE FROM folders WHERE id IN (:b, :l)", {"b": briefs, "l": legal})
    restorer = str(uuid.uuid4())  # never written (no such user): the file is held already

    with db.customer(c) as conn:
        assert trash.restore_document(conn, c, document, by=restorer) == trash.Placed(held=True)

    assert _where(db, document) == (None, holder)


def test_a_restore_reads_whether_the_department_is_live_only_under_the_tree_lock(db: Database) -> None:
    """A delete holds the tree lock; the restore waits for it, then finds the department gone and
    holds the file (the department's top was live when it asked)."""
    api = Api(db)
    org = _walled(api)
    member = org.people["member"]
    legal = _department(api, org, "Legal", member)
    briefs = api.add_folder(org.c, member.user_id, "Briefs", legal)
    brief = api.add_document(org.c, name="brief.pdf", folder_id=briefs, uploaded_by=member.user_id)
    _bin(member, brief)
    _gone(org, briefs)
    replies: list[httpx.Response] = []

    def restore() -> None:
        replies.append(member.client.post(f"/api/documents/{brief}/restore", headers=CSRF))

    with db.customer(org.c) as conn:  # as a delete
        folders_sql._lock_tree(conn, org.c)
        restoring = threading.Thread(target=restore)
        restoring.start()
        restoring.join(timeout=1)
        assert restoring.is_alive()
        folders_sql.delete_folder(conn, org.c, legal)
    restoring.join(timeout=10)

    assert replies[0].status_code == 200, replies[0].text
    assert replies[0].json()["placed"] == {"held": True, "folder": None}
    assert _where(db, brief) == (None, member.user_id)


# --- path 2: a held file linked as a new version, then undone ----------------------------------


def _link(db: Database, org: Org, new: str, family: str, signed: Signed) -> None:
    suggestion = str(_owner(db, _SUGGEST, {"c": org.c, "d": new, "f": family})[0][0])
    reply = signed.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF)
    assert reply.status_code == 200, reply.text


def _undo(signed: Signed, document: str) -> httpx.Response:
    reply: httpx.Response = signed.client.post(f"/api/documents/{document}/version-undo", headers=CSRF)
    return reply


def test_a_held_file_linked_then_undone_is_held_again_for_the_same_person(db: Database) -> None:
    """``held.pdf`` is held for ``member``; linked into Sales (open) it is in Sales. Its Undo by
    the plain reviewer (who sees it in Sales) would hold it for another: 403, nothing changed.
    Management's Undo holds it for ``member`` again, not for Management."""
    api = Api(db)
    org = _walled(api)
    held, member = org.files["held"], org.people["member"]
    _link(db, org, held, org.files["sales"], member)
    assert _where(db, held) == (org.folders["sales"], None)
    kept = "SELECT previous_held_for FROM version_suggestions WHERE document_id = :d"
    assert [str(r[0]) for r in _owner(db, kept, {"d": held})] == [member.user_id]

    refused = _undo(org.people["plain"], held)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden"), refused.text
    assert _where(db, held) == (org.folders["sales"], None)

    assert _undo(org.people["management"], held).status_code == 200
    assert _where(db, held) == (None, member.user_id)
    assert _sees(db, org, held, "member", "management", "plain", "head") == {"member", "management"}
    assert _differences(db, org.c) == []


def test_a_file_in_no_folder_held_for_nobody_linked_then_undone_is_open_as_before(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    top, admin = org.files["top"], org.people["admin"]
    _link(db, org, top, org.files["sales"], admin)

    assert _undo(org.people["plain"], top).status_code == 200

    assert _where(db, top) == (None, None)
    assert _sees(db, org, top, "plain", "member") == {"plain", "member"}


def test_an_undo_whose_folder_is_gone_goes_no_wider_than_the_family_it_leaves(db: Database) -> None:
    """Inbox (open) is deleted after its files were linked. Into Budgets (in Finance, Department
    only), the Undo puts it at the top of Finance; into Sales (open), in no folder, as before."""
    api = Api(db)
    org = _walled(api)
    member = org.people["member"]
    inbox = api.add_folder(org.c, member.user_id, "Inbox")
    budgets = api.add_folder(org.c, member.user_id, "Budgets", org.folders["finance"])
    family = api.add_document(org.c, name="plan.pdf", folder_id=budgets, uploaded_by=member.user_id)
    walled = api.add_document(org.c, name="plan2.pdf", folder_id=inbox, uploaded_by=member.user_id)
    open_one = api.add_document(org.c, name="sales2.pdf", folder_id=inbox, uploaded_by=member.user_id)
    _link(db, org, walled, family, member)
    _link(db, org, open_one, org.files["sales"], member)
    _gone(org, inbox)

    for document in (walled, open_one):
        assert _undo(member, document).status_code == 200

    assert _where(db, walled) == (org.folders["finance"], None)
    assert _sees(db, org, walled, "member", "plain") == {"member"}
    assert _where(db, open_one) == (None, None)
    assert _differences(db, org.c) == []


# --- path 3: a folder deleted with its contents kept ---------------------------------------------


def _state(db: Database, org: Org) -> tuple[Any, ...]:
    folders = _owner(db, "SELECT id, parent_id FROM folders WHERE customer_id = :c ORDER BY id", {"c": org.c})
    trashed = _owner(
        db, "SELECT id, parent_id FROM trashed_folders WHERE customer_id = :c ORDER BY id", {"c": org.c}
    )
    documents = _owner(
        db,
        "SELECT id, folder_id, department_id, private_folder_id FROM documents WHERE customer_id = :c"
        " ORDER BY id",
        {"c": org.c},
    )
    return folders, trashed, documents, _version(db, org)


def _keep(org: Org, folder: str, to: str | None = None) -> httpx.Response:
    where = "" if to is None else f"&to={to}"
    return org.people["admin"].client.delete(f"/api/folders/{folder}?contents=keep{where}", headers=CSRF)


def _refused(reply: httpx.Response) -> None:
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "access_would_change"), reply.text


def test_keeping_a_department_only_departments_contents_at_the_top_level_is_refused(db: Database) -> None:
    org = _walled(Api(db))
    before = _state(db, org)
    _refused(_keep(org, org.folders["finance"]))
    assert _state(db, org) == before


def test_keeping_a_private_folders_contents_in_its_open_parent_is_refused(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    sales = org.folders["sales"]
    vault = api.add_folder(org.c, org.people["admin"].user_id, "Vault", sales)
    _private(db, org, vault)
    api.add_document(org.c, name="vault.pdf", folder_id=vault, uploaded_by=org.people["admin"].user_id)
    before = _state(db, org)
    _refused(_keep(org, vault, sales))  # the dialog's default destination
    assert _state(db, org) == before


def test_a_restricted_folder_holding_only_a_folder_in_trash_is_refused_too(db: Database) -> None:
    """Its child folder in Trash would restore at the top level, open."""
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    budgets = api.add_folder(org.c, admin.user_id, "Budgets", org.folders["finance"])
    inner = api.add_folder(org.c, admin.user_id, "Inner", budgets)
    api.add_document(org.c, name="inner.pdf", folder_id=inner, uploaded_by=admin.user_id)
    assert admin.client.delete(f"/api/folders/{inner}?contents=trash", headers=CSRF).status_code == 200
    before = _state(db, org)
    _refused(_keep(org, budgets))
    assert _state(db, org) == before


def test_keeping_contents_where_the_same_people_see_them_still_works(db: Database) -> None:
    """An open department to the top level; a plain folder of Finance into Finance."""
    api = Api(db)
    org = _walled(api)
    finance = org.folders["finance"]
    budgets = api.add_folder(org.c, org.people["admin"].user_id, "Budgets", finance)
    plan = api.add_document(
        org.c, name="plan.pdf", folder_id=budgets, uploaded_by=org.people["admin"].user_id
    )

    for reply in (_keep(org, org.folders["sales"]), _keep(org, budgets, finance)):
        assert reply.status_code == 200, reply.text

    assert _where(db, plan) == (finance, None)
    assert _where(db, org.files["sales"]) == (None, None)
    assert _differences(db, org.c) == []


# --- a folder restored with its folder gone ------------------------------------------------------


def _trash_folder(org: Org, folder: str) -> None:
    reply = org.people["admin"].client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text


def _restore_folder(org: Org, folder: str) -> dict[str, Any]:
    reply = org.people["admin"].client.post(f"/api/documents/trash/folders/{folder}/restore", headers=CSRF)
    assert reply.status_code == 200, reply.text
    body: dict[str, Any] = reply.json()
    return body


def _inner(api: Api, org: Org, parent: str, by: Signed) -> tuple[str, str, str]:
    """``parent`` > Middle > Inner (holding a file of ``by``): Inner goes to Trash, then Middle is
    deleted. Returns (Middle, Inner, the file)."""
    middle = api.add_folder(org.c, by.user_id, "Middle", parent)
    inner = api.add_folder(org.c, by.user_id, "Inner", middle)
    document = api.add_document(org.c, name="inner.pdf", folder_id=inner, uploaded_by=by.user_id)
    _trash_folder(org, inner)
    _gone(org, middle)
    return middle, inner, document


def _parent_and_private(db: Database, folder: str) -> tuple[str | None, bool]:
    parent, private = _owner(db, "SELECT parent_id, private FROM folders WHERE id = :f", {"f": folder})[0]
    return (None if parent is None else str(parent)), bool(private)


def test_a_folder_whose_folder_went_comes_back_under_its_live_department(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    finance = org.folders["finance"]
    _, inner, document = _inner(api, org, finance, org.people["member"])

    body = _restore_folder(org, inner)

    assert (body["relocated"], body["sealed"], body["location"]) == (True, False, ["Finance"])
    assert _parent_and_private(db, inner) == (finance, False)
    assert _sees(db, org, document, "member", "plain") == {"member"}
    restored = [row[3] for row in api.audit_rows(org.c) if row[0] == "folder.restore"]
    assert restored[0]["to"] == finance and "sealed" not in restored[0]
    assert _differences(db, org.c) == []


def test_a_folder_whose_folder_went_comes_back_under_its_live_private_folder(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    salaries = org.folders["salaries"]
    _, inner, document = _inner(api, org, salaries, org.people["named"])

    body = _restore_folder(org, inner)

    assert (body["relocated"], body["sealed"], body["location"]) == (True, False, ["Finance", "Salaries"])
    assert _parent_and_private(db, inner) == (salaries, False)
    assert _sees(db, org, document, "named", "member", "plain") == {"named"}
    assert _differences(db, org.c) == []


def test_a_folder_whose_department_went_comes_back_private_with_nobody_named(db: Database) -> None:
    """Q6: Legal was open; its folder comes back at the top level, private: Management sees its
    file, nobody else does until an Admin names people."""
    api = Api(db)
    org = _walled(api)
    legal = _department(api, org, "Legal")
    inner = api.add_folder(org.c, org.people["admin"].user_id, "Inner", legal)
    document = api.add_document(
        org.c, name="inner.pdf", folder_id=inner, uploaded_by=org.people["admin"].user_id
    )
    _trash_folder(org, inner)
    _gone(org, legal)

    body = _restore_folder(org, inner)

    assert (body["relocated"], body["sealed"], body["location"]) == (True, True, [])
    assert _parent_and_private(db, inner) == (None, True)
    assert _sees(db, org, document, "management", "plain", "member") == {"management"}
    restored = [row[3] for row in api.audit_rows(org.c) if row[0] == "folder.restore"]
    assert (restored[0]["to"], restored[0]["sealed"]) == (None, True)
    assert _differences(db, org.c) == []


def test_a_folder_whose_private_folder_went_comes_back_private_under_its_department(db: Database) -> None:
    """Q1 for folders: Vault (private, in Finance) is gone. Under Finance it would show to every
    member, so it comes back private there: Finance's head and Management see its file."""
    api = Api(db)
    org = _walled(api)
    finance, named = org.folders["finance"], org.people["named"]
    vault = api.add_folder(org.c, named.user_id, "Vault", finance)
    _private(db, org, vault, named.user_id)
    _, inner, document = _inner(api, org, vault, named)
    _gone(org, vault)

    body = _restore_folder(org, inner)

    assert (body["relocated"], body["sealed"], body["location"]) == (True, True, ["Finance"])
    assert _parent_and_private(db, inner) == (finance, True)
    assert _sees(db, org, document, "head", "management", "member", "named", "plain") == {
        "head",
        "management",
    }
    assert _differences(db, org.c) == []
