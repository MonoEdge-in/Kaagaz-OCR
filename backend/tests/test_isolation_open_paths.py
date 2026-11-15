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

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.db import folders as folders_sql
from kaagaz.db import trash
from kaagaz.db import trashed_folders as trashed_sql
from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_folder, name_session, new_customer, wait_until_blocked
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
    """The restorer is never written over a holder the file already has (walled: Legal is
    Department only)."""
    c, holder = new_customer(db)
    with db.customer(c) as conn:
        legal = insert_folder(conn, c, holder, "Legal")
        briefs = insert_folder(conn, c, holder, "Briefs", legal)
    _owner(db, "UPDATE folders SET kind = 'department', department_only = true WHERE id = :f", {"f": legal})
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
    session = f"restore-{uuid.uuid4().hex[:16]}"

    def restore() -> tuple[trash.Placed | None, Any]:
        with db.user(org.c, member.user_id) as conn:  # the route's restore, as the member
            name_session(conn, session)
            return trashed_sql.restore_document(conn, org.c, brief, admin=False, by=member.user_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(org.c) as conn:  # as a delete
            folders_sql._lock_tree(conn, org.c)
            restoring = pool.submit(restore)
            wait_until_blocked(db, application_name=session)
            folders_sql.delete_folder(conn, org.c, legal)
        assert restoring.result(timeout=10) == (trash.Placed(held=True), None)

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
    # Changed on purpose (#601, PR 10c): the join into Finance changes who sees it, so Management's.
    _link(db, org, walled, family, org.people["management"])
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


# --- the organisation's walls (0089: customers.walls_set) ----------------------------------------


def _stranded(api: Api, *, department_only: bool) -> tuple[str, str, str, str]:
    """A new organisation whose only department, Legal (Department only or not), held Briefs and
    its file: the file went to Trash, then Briefs and Legal were deleted for good. Returns (the
    organisation, the file, a member in no department, a Management member)."""
    db = api.db
    c, first = api.customer()
    plain, _ = api.add_user(c, "member")
    management, _ = api.add_user(c, "member")
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": management})
    legal = api.add_folder(c, first, "Legal")
    briefs = api.add_folder(c, first, "Briefs", legal)
    _owner(
        db,
        "UPDATE folders SET kind = 'department', department_only = :o WHERE id = :f",
        {"o": department_only, "f": legal},
    )
    brief = api.add_document(c, name="brief.pdf", folder_id=briefs, uploaded_by=first)
    _owner(
        db, "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d", {"u": first, "d": brief}
    )
    _owner(db, "UPDATE documents SET folder_id = NULL WHERE id = :d", {"d": brief})  # as a folder delete does
    _owner(db, "DELETE FROM folders WHERE id IN (:b, :l)", {"b": briefs, "l": legal})
    return c, brief, plain, management


def _walls(db: Database, customer: str) -> bool:
    return bool(_owner(db, "SELECT walls_set FROM customers WHERE id = :c", {"c": customer})[0][0])


def test_day_one_nothing_is_walled_and_nothing_changes(db: Database) -> None:
    """No folder was ever Department only: the flag is false and a department deleted since is
    open, as before."""
    api = Api(db)
    c, brief, plain, _ = _stranded(api, department_only=False)
    assert not _walls(db, c)
    assert brief in _seen(_app(db), c, plain)


def test_the_last_department_only_department_deleted_for_good_leaves_its_trash_closed(db: Database) -> None:
    """0087's known gap: Legal was the organisation's only Department only department. Its file
    stranded in Trash stays closed: Management sees it, the member in no department doesn't."""
    api = Api(db)
    c, brief, plain, management = _stranded(api, department_only=True)
    assert _walls(db, c)
    assert brief not in _seen(_app(db), c, plain)
    assert brief in _seen(_app(db), c, management)


def test_the_walls_flag_is_set_from_a_folder_in_trash_too_and_never_goes_back(db: Database) -> None:
    api = Api(db)
    c, first = api.customer()
    inbox = api.add_folder(c, first, "Inbox")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": inbox})
    _owner(
        db,
        "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, created_by, created_at,"
        " trash_id, root, deleted_at, deleted_by) VALUES (gen_random_uuid(), :c, NULL, 'Old', 'department',"
        " :u, now(), gen_random_uuid(), true, now(), :u)",
        {"c": c, "u": first},
    )
    assert not _walls(db, c)
    _owner(db, "UPDATE trashed_folders SET department_only = true WHERE customer_id = :c", {"c": c})
    assert _walls(db, c)
    _owner(db, "UPDATE folders SET department_only = true WHERE id = :f", {"f": inbox})
    _owner(db, "UPDATE folders SET department_only = false WHERE id = :f", {"f": inbox})
    _owner(db, "DELETE FROM trashed_folders WHERE customer_id = :c", {"c": c})
    _owner(db, "DELETE FROM folders WHERE id = :f", {"f": inbox})
    assert _walls(db, c)


# --- the reviews' cases (#594) --------------------------------------------------------------------


def _undone(api: Api, org: Org) -> list[dict[str, Any]]:
    return [row[3] for row in api.audit_rows(org.c) if row[0] == "version.undone"]


def test_an_undo_of_a_version_a_restore_held_keeps_it_held(db: Database) -> None:
    """plan.pdf (Briefs, in Legal) has a version linked from Inbox. The family goes to Trash, Inbox,
    Briefs and Legal are deleted, and the restore holds the family for the member. Undo of that
    version: its previous folder is gone and it is in no folder, so it stays held for them."""
    api = Api(db)
    org = _walled(api)
    member = org.people["member"]
    legal = _department(api, org, "Legal", member)
    briefs = api.add_folder(org.c, member.user_id, "Briefs", legal)
    inbox = api.add_folder(org.c, member.user_id, "Inbox")
    plan = api.add_document(org.c, name="plan.pdf", folder_id=briefs, uploaded_by=member.user_id)
    draft = api.add_document(org.c, name="plan2.pdf", folder_id=inbox, uploaded_by=member.user_id)
    _link(db, org, draft, plan, member)
    _bin(member, plan)
    for folder in (inbox, briefs, legal):
        _gone(org, folder)
    assert _back(member, plan) == {"held": True, "folder": None}

    assert _undo(member, draft).status_code == 200

    assert _where(db, draft) == (None, member.user_id)
    assert _sees(db, org, draft, "member", "plain", "head") == {"member"}
    assert _undone(api, org)[-1]["held"] is True
    assert _differences(db, org.c) == []


def test_a_held_family_a_filed_document_is_linked_to_stays_held(db: Database) -> None:
    """held.pdf is held for the member, in no folder. A Sales file linked to it does not take the
    family into Sales (issue 414's move): both stay held for the member. Undo puts the Sales file
    back in Sales; the family stays held."""
    api = Api(db)
    org = _walled(api)
    member, held, sales = org.people["member"], org.files["held"], org.folders["sales"]
    memo = api.add_document(org.c, name="memo.pdf", folder_id=sales, uploaded_by=member.user_id)

    _link(db, org, memo, held, member)

    assert [_where(db, d) for d in (held, memo)] == [(None, member.user_id)] * 2
    assert _sees(db, org, memo, "member", "plain", "management") == {"member", "management"}
    assert _undo(member, memo).status_code == 200
    assert _where(db, memo) == (sales, None)
    assert _where(db, held) == (None, member.user_id)
    assert _differences(db, org.c) == []


def test_the_holder_may_undo_the_link_of_their_held_file(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    held, member = org.files["held"], org.people["member"]
    _link(db, org, held, org.files["sales"], member)

    assert _undo(member, held).status_code == 200

    assert _where(db, held) == (None, member.user_id)


def test_an_undo_whose_folder_is_gone_goes_into_the_familys_private_folder(db: Database) -> None:
    """A file from Inbox linked into a Salaries family; Inbox is deleted. Undo puts it in Salaries
    (its private folder before its department): the Finance member can't see it."""
    api = Api(db)
    org = _walled(api)
    named, salaries = org.people["named"], org.folders["salaries"]
    inbox = api.add_folder(org.c, named.user_id, "Inbox")
    note = api.add_document(org.c, name="sal2.pdf", folder_id=inbox, uploaded_by=named.user_id)
    # Changed on purpose (#601, PR 10c): the join into Salaries changes who sees it, so Management's.
    _link(db, org, note, org.files["sal"], org.people["management"])
    _gone(org, inbox)

    assert _undo(named, note).status_code == 200

    assert _where(db, note) == (salaries, None)
    assert _sees(db, org, note, "named", "member") == {"named"}
    assert _differences(db, org.c) == []


def test_a_file_restored_into_a_folder_the_restorer_cant_see_names_no_folder(db: Database) -> None:
    """The plain reviewer was given their own file in Budgets (in Finance, which they can't see).
    Budgets is gone: it goes to the top of Finance, and the reply names no folder."""
    api = Api(db)
    org = _walled(api)
    plain, finance = org.people["plain"], org.folders["finance"]
    budgets = api.add_folder(org.c, plain.user_id, "Budgets", finance)
    mine = api.add_document(org.c, name="claim.pdf", folder_id=budgets, uploaded_by=plain.user_id)
    _owner(
        db,
        "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": org.c, "d": mine, "u": plain.user_id},
    )
    _bin(plain, mine)
    _gone(org, budgets)

    reply = plain.client.post(f"/api/documents/{mine}/restore", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert reply.json()["placed"] == {"held": False, "folder": None}
    assert "Finance" not in reply.text
    assert _where(db, mine) == (finance, None)


def test_a_department_or_private_folder_restored_at_the_top_level_is_not_sealed(db: Database) -> None:
    """Their own department or private folder is not one they inherited, gone while in Trash."""
    api = Api(db)
    org = _walled(api)
    legal = _department(api, org, "Legal")
    vault = api.add_folder(org.c, org.people["admin"].user_id, "Vault")
    _private(db, org, vault)
    for folder in (legal, vault):
        _trash_folder(org, folder)
        body = _restore_folder(org, folder)
        assert (body["relocated"], body["sealed"]) == (False, False)
    assert [_parent_and_private(db, f) for f in (legal, vault)] == [(None, False), (None, True)]


def test_a_folder_made_department_only_as_it_is_added_sets_the_flag_and_the_access_version(
    db: Database,
) -> None:
    api = Api(db)
    c, first = api.customer()
    version = "SELECT access_version FROM customers WHERE id = :c"
    before = int(_owner(db, version, {"c": c})[0][0])
    _owner(
        db,
        "INSERT INTO folders (customer_id, name, created_by, kind, department_only)"
        " VALUES (:c, 'Legal', :u, 'department', true)",
        {"c": c, "u": first},
    )
    assert _walls(db, c)
    assert int(_owner(db, version, {"c": c})[0][0]) == before + 1


def _unwalled(api: Api) -> tuple[str, Signed, Signed, Signed]:
    """A new organisation with nothing walled: (it, its admin, a member, another member)."""
    c, _ = api.customer()
    return c, api.admin_sign_in(c), api.sign_in(c), api.sign_in(c)


def test_day_one_a_restore_whose_department_went_comes_back_open_by_every_route(db: Database) -> None:
    """Decision 3: no organisation wall, so no seal and no hold. Legal (open) held Inner, Inner2
    and Briefs; Inner and Inner2 went to Trash, Briefs' file too; then Briefs and Legal were
    deleted. A member restores Inner (the folder route), Inner2's file (the file route, its folder
    with it) and Briefs' file: all come back open, in no department."""
    api = Api(db)
    c, admin, member, other = _unwalled(api)
    legal = api.add_folder(c, admin.user_id, "Legal")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": legal})
    inner, inner2, briefs = (
        api.add_folder(c, member.user_id, n, legal) for n in ("Inner", "Inner2", "Briefs")
    )
    files = [
        api.add_document(c, name=f"{n}.pdf", folder_id=f, uploaded_by=member.user_id)
        for n, f in (("a", inner), ("b", inner2), ("c", briefs))
    ]
    for folder in (inner, inner2):
        assert admin.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF).status_code == 200
    assert member.client.delete(f"/api/documents/{files[2]}", headers=CSRF).status_code == 204
    for folder in (briefs, legal):
        assert admin.client.delete(f"/api/folders/{folder}", headers=CSRF).status_code == 204

    body = member.client.post(f"/api/documents/trash/folders/{inner}/restore", headers=CSRF).json()
    back = member.client.post(f"/api/documents/{files[1]}/restore", headers=CSRF).json()
    placed = member.client.post(f"/api/documents/{files[2]}/restore", headers=CSRF).json()["placed"]

    assert (body["relocated"], body["sealed"], body["location"]) == (True, False, [])
    assert back["restored_folder"]["sealed"] is False and placed is None
    assert [_parent_and_private(db, f) for f in (inner, inner2)] == [(None, False)] * 2
    assert _where(db, files[2]) == (None, None)
    assert not _walls(db, c)
    assert set(files) <= _seen(_app(db), c, other.user_id)


def test_a_named_person_restoring_what_their_private_folder_lost_is_named_on_the_seal(
    db: Database,
) -> None:
    """Walled: Vault (private, in Finance, names ``named``) held Inner and Inner2; both went to
    Trash and Vault was deleted. ``named`` restores Inner (the folder route) and Inner2's file (the
    file route): each comes back sealed under Finance, with ``named`` named on it, so they still
    see it; the Finance member does not."""
    api = Api(db)
    org = _walled(api)
    named, finance = org.people["named"], org.folders["finance"]
    vault = api.add_folder(org.c, named.user_id, "Vault", finance)
    _private(db, org, vault, named.user_id)
    inner, inner2 = (api.add_folder(org.c, named.user_id, n, vault) for n in ("Inner", "Inner2"))
    files = [
        api.add_document(org.c, name=f"{n}.pdf", folder_id=f, uploaded_by=named.user_id)
        for n, f in (("a", inner), ("b", inner2))
    ]
    for folder in (inner, inner2):
        _trash_folder(org, folder)
    _gone(org, vault)

    body = named.client.post(f"/api/documents/trash/folders/{inner}/restore", headers=CSRF)
    back = named.client.post(f"/api/documents/{files[1]}/restore", headers=CSRF)

    assert body.status_code == 200, body.text
    assert back.status_code == 200, back.text
    assert body.json()["sealed"] is True and back.json()["restored_folder"]["sealed"] is True
    assert [_parent_and_private(db, f) for f in (inner, inner2)] == [(finance, True)] * 2
    members = "SELECT folder_id FROM private_folder_members WHERE user_id = :u"
    assert {inner, inner2} <= {str(r[0]) for r in _owner(db, members, {"u": named.user_id})}
    for document in files:
        assert _sees(db, org, document, "named", "member") == {"named"}
    assert _differences(db, org.c) == []
