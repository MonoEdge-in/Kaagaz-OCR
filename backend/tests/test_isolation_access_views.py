"""PRD 7 PR 6 (#595): the access views (0086), and the lists, counts and links that read them.

Walls are set by hand in the database (as the owner, as an Admin's screens will in PR 10):
Finance is Department only, "Salaries" inside it is private, a file in no folder is held for a
person, a Sales file is locked and one person may open it, one Finance file is given to that
person, a Salaries file is in Trash. The views are checked against a plain Python version of
R0 to R5 (section 4) for every person, and every list, count and open-by-id route the API has
is checked as the person who cannot see a file: it is not there, and asking for it by id is
answered exactly as for an id that never existed."""

import ast
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

import kaagaz
from kaagaz.db import audit, listing
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.scope import EVERYTHING
from tests.db_support import APP_ROLE, insert_folder
from tests.test_api_delete_forever_grant import _confirm, _grant
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_isolation_logins import WORKER
from tests.test_operator_auth_db import _owner
from tests.test_search_db import RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

_DEPARTMENTS = (
    "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
    " VALUES (:c, :d, :u, :head, :by)"
)
# Changed on purpose (#599, 0087): the open departments, where 0086 listed the closed ones.
_ACCESS = "SELECT departments, heads, named, open FROM kaagaz_access()"
_SUGGEST = (
    "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, text_overlap,"
    " name_similarity, outcome, status) VALUES (:c, :d, :f, 0.86, 0.31, 'suggested', 'pending') RETURNING id"
)


@dataclass
class Org:
    """One walled organisation: its people (Signed, each with a session) and files by name."""

    c: str
    people: dict[str, Signed]
    folders: dict[str, str]
    files: dict[str, str]


def _app(db: Database) -> Database:
    return Database(db.engine, APP_ROLE)


def _seen(database: Database, customer: str, user: str | None, view: str = "visible_documents") -> set[str]:
    """The ids ``view`` (one of two fixed names) holds for ``user`` (None: no person set)."""
    with database.customer(customer) if user is None else database.user(customer, user) as conn:
        return {str(row[0]) for row in conn.execute(text(f"SELECT id FROM {view}"))}  # noqa: S608


def _all(db: Database, customer: str, table: str = "documents") -> set[str]:
    rows = _owner(db, f"SELECT id FROM {table} WHERE customer_id = :c", {"c": customer})  # noqa: S608
    return {str(row[0]) for row in rows}


def _walled(api: Api) -> Org:
    """The organisation of the module docstring. People: ``management``, ``head`` (of Finance),
    ``member`` (of Finance), ``plain`` (a reviewer in no department, given one Finance file and
    allowed to open the locked one), ``named`` (on Salaries), ``admin`` and ``off`` (switched off)."""
    db = api.db
    c, first = api.customer()
    people = {
        name: api.sign_in(c, "reviewer" if name == "plain" else "member")
        for name in ("management", "head", "member", "plain", "named", "off")
    }
    people["admin"] = api.admin_sign_in(c)
    with db.customer(c) as conn:
        finance = insert_folder(conn, c, first, "Finance")
        sales = insert_folder(conn, c, first, "Sales")
        salaries = insert_folder(conn, c, first, "Salaries", finance)
        loose = insert_folder(conn, c, first, "Loose")
    folders = {"finance": finance, "sales": sales, "salaries": salaries, "loose": loose}
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id IN (:f, :s)", {"f": finance, "s": sales})
    _owner(db, "UPDATE folders SET department_only = true WHERE id = :f", {"f": finance})
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": salaries})
    files = {
        "fin": api.add_document(c, name="fin.pdf", folder_id=finance, uploaded_by=first),
        "sal": api.add_document(c, name="sal.pdf", folder_id=salaries, uploaded_by=first),
        "sales": api.add_document(c, name="sales.pdf", folder_id=sales, uploaded_by=first),
        "top": api.add_document(c, name="top.pdf", uploaded_by=first),
        "loose": api.add_document(c, name="loose.pdf", folder_id=loose, uploaded_by=first),
        "held": api.add_document(c, name="held.pdf", uploaded_by=first),
        "locked": api.add_document(c, name="locked.pdf", folder_id=sales, uploaded_by=first),
        "given": api.add_document(c, name="given.pdf", folder_id=finance, uploaded_by=first),
        "trashed": api.add_document(c, name="trashed.pdf", folder_id=salaries, uploaded_by=first),
    }
    ids = {name: signed.user_id for name, signed in people.items()}
    for user, head in ((ids["member"], False), (ids["head"], True)):
        _owner(db, _DEPARTMENTS, {"c": c, "d": finance, "u": user, "head": head, "by": first})
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :by)",
        {"c": c, "f": salaries, "u": ids["named"], "by": first},
    )
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": ids["management"]})
    _owner(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": ids["off"]})
    _owner(db, "UPDATE documents SET held_for = :u WHERE id = :d", {"u": ids["member"], "d": files["held"]})
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": first, "d": files["locked"]},
    )
    _owner(
        db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": first, "d": files["trashed"]},
    )
    for table, document in (("document_shares", "given"), ("lock_grants", "locked")):
        _owner(
            db,
            f"INSERT INTO {table} (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)",  # noqa: S608
            {"c": c, "d": files[document], "u": ids["plain"], "by": first},
        )
    return Org(c, people, folders, files)


def _expected(db: Database, org: Org, user: str, *, folders: bool = False) -> set[str]:
    """R0 to R5 (folders: R1, R4, R5 and Admins) written plainly, from the stored values."""
    c = org.c
    me = _owner(db, "SELECT management, role = 'admin', disabled FROM users WHERE id = :u", {"u": user})[0]
    management, admin, disabled = bool(me[0]), bool(me[1]), bool(me[2])
    if disabled:
        return set()
    member = {
        str(r[0])
        for r in _owner(db, "SELECT department_id FROM department_members WHERE user_id = :u", {"u": user})
    }
    heads = {
        str(r[0])
        for r in _owner(
            db, "SELECT department_id FROM department_members WHERE user_id = :u AND is_head", {"u": user}
        )
    }
    named = {
        str(r[0])
        for r in _owner(db, "SELECT folder_id FROM private_folder_members WHERE user_id = :u", {"u": user})
    }
    # Changed on purpose (#599, 0087): open is a department still there (live or in Trash) and not
    # Department only; one deleted since is closed (fail closed).
    opened = {
        str(r[0])
        for r in _owner(
            db,
            "SELECT id FROM folders WHERE customer_id = :c AND parent_id IS NULL AND kind = 'department'"
            " AND NOT department_only UNION ALL SELECT id FROM trashed_folders WHERE customer_id = :c"
            " AND parent_id IS NULL AND kind = 'department' AND NOT department_only",
            {"c": c},
        )
    }

    def place(department: Any, private: Any, held: Any) -> bool:
        department, private = (None if v is None else str(v) for v in (department, private))
        r5 = (
            private is None
            and held is None
            and (department is None or department in opened or department in member)
        )
        r4 = private is not None and (private in named or department in heads)
        return r5 or r4

    if folders:
        rows = _owner(
            db, "SELECT id, department_id, private_folder_id FROM folders WHERE customer_id = :c", {"c": c}
        )
        return {str(i) for i, d, p in rows if management or admin or place(d, p, None)}
    given = {
        str(r[0])
        for r in _owner(db, "SELECT document_id FROM document_shares WHERE user_id = :u", {"u": user})
    }
    may_open = {
        str(r[0]) for r in _owner(db, "SELECT document_id FROM lock_grants WHERE user_id = :u", {"u": user})
    }
    rows = _owner(
        db,
        "SELECT id, department_id, private_folder_id, held_for, locked_at FROM documents"
        " WHERE customer_id = :c",
        {"c": c},
    )
    seen = set()
    for i, d, p, held, locked in rows:
        i = str(i)
        r0 = locked is None or i in may_open
        r3 = held is not None and str(held) == user
        if r0 and (management or place(d, p, held) or r3 or i in given):
            seen.add(i)
    return seen


# --- the views ----------------------------------------------------------------------------


def test_the_views_follow_the_written_rule_for_every_person(db: Database) -> None:
    org = _walled(Api(db))
    app, f = _app(db), org.files
    for name, signed in org.people.items():
        for folders, view in ((False, "visible_documents"), (True, "visible_folders")):
            expected = _expected(db, org, signed.user_id, folders=folders)
            assert _seen(app, org.c, signed.user_id, view) == expected, (name, view)
    seen = {name: _seen(app, org.c, signed.user_id) for name, signed in org.people.items()}
    # The parts named once more, so a mistake shared by the rule above and the view still fails.
    assert f["locked"] not in seen["management"] | seen["admin"]  # R0 comes first, for everyone
    assert {f["locked"], f["given"]} <= seen["plain"] and f["fin"] not in seen["plain"]  # R2, R0
    assert {f["sal"], f["trashed"]} <= seen["head"] & seen["named"]  # R4: the head, the named
    assert f["sal"] not in seen["member"] and f["fin"] in seen["member"]  # private beats member
    assert f["held"] in seen["member"] and f["held"] not in seen["plain"]  # R3
    assert {f["top"], f["loose"], f["sales"]} <= seen["plain"]  # R5: open
    assert seen["off"] == set()
    assert org.folders["finance"] in _seen(app, org.c, org.people["admin"].user_id, "visible_folders")
    assert org.folders["finance"] not in _seen(app, org.c, org.people["plain"].user_id, "visible_folders")


def test_day_one_every_person_sees_the_whole_organisation(db: Database) -> None:
    api = Api(db)
    c, first = api.customer()
    with db.customer(c) as conn:
        folder = insert_folder(conn, c, first, "Finance")
        sub = insert_folder(conn, c, first, "Bills", folder)
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": folder})
    for place in (folder, sub, None):
        api.add_document(c, folder_id=place, uploaded_by=first)
    trashed = api.add_document(c, folder_id=sub, uploaded_by=first)
    _owner(
        db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": first, "d": trashed},
    )
    people = [first] + [api.add_user(c, role)[0] for role in ("member", "reviewer")]
    for user in people:
        assert _seen(_app(db), c, user) == _all(db, c)
        assert _seen(_app(db), c, user, "visible_folders") == _all(db, c, "folders")


def test_who_asks(db: Database) -> None:
    """The worker role with no person: its whole organisation and no other. The API role with no
    person, an unknown or switched-off person, or another organisation's: nothing. The worker
    acting for a person: that person's rule."""
    api = Api(db)
    org, other = _walled(api), _walled(api)
    worker, app = Database(db.engine, WORKER), _app(db)
    for view, table in (("visible_documents", "documents"), ("visible_folders", "folders")):
        assert _seen(worker, org.c, None, view) == _all(db, org.c, table)
        assert _seen(app, org.c, None, view) == set()
        for stranger in (str(uuid.uuid4()), org.people["off"].user_id, other.people["management"].user_id):
            assert _seen(app, org.c, stranger, view) == set()
            assert _seen(worker, org.c, stranger, view) == set()
    plain = org.people["plain"].user_id
    assert _seen(worker, org.c, plain) == _seen(app, org.c, plain) == _expected(db, org, plain)


def test_the_access_arrays_are_never_null(db: Database) -> None:
    """Condition 2 on #595: an empty array, never NULL, so ``= ANY (a.open)`` (0087; 0086's
    ``<> ALL (a.closed)``) can't become NULL."""
    api = Api(db)
    org = _walled(api)
    bare, bare_user = api.customer()
    app = _app(db)
    for customer, user in (
        (org.c, None),
        (org.c, str(uuid.uuid4())),
        (bare, bare_user),
        (org.c, org.people["plain"].user_id),
    ):
        with app.customer(customer) if user is None else app.user(customer, user) as conn:
            arrays = conn.execute(text(_ACCESS)).one()
        assert all(isinstance(each, list) for each in arrays), (customer, user, arrays)
    with app.user(bare, bare_user) as conn:
        assert tuple(conn.execute(text(_ACCESS)).one()) == ([], [], [], [])
    # A person in no department still sees an open file in an organisation with a closed one.
    assert org.files["sales"] in _seen(app, org.c, org.people["plain"].user_id)


def _plan(db: Database, customer: str, user: str, statement: str, params: dict[str, Any]) -> dict[str, Any]:
    with _app(db).user(customer, user) as conn:
        found = conn.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + statement), params).scalar_one()
    plan: dict[str, Any] = (json.loads(found) if isinstance(found, str) else found)[0]["Plan"]
    return plan


def _nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [node] + [each for child in node.get("Plans", []) for each in _nodes(child)]


def test_day_one_never_runs_the_given_or_lock_lookups(db: Database) -> None:
    """With nothing walled, every row passes on its own columns: the files-given and lock-grant
    sets are never read, and the access set is worked out by one Function Scan per view named."""
    api = Api(db)
    c, first = api.customer()
    document = api.add_document(c, uploaded_by=first)
    for _ in range(5):
        api.add_document(c, uploaded_by=first)
    page = text(
        listing._COLUMNS + listing._LIST + listing._FILTERS + " ORDER BY d.created_at DESC LIMIT 50"
    ).text
    # PR 8 (#602): the meaning walk and the word list too.
    add_pieces(db, c, document, [("Invoice ledger", axis(0))])
    words = {"q": "ledger", "k": "ledger", "g": "", "sk": "", "n": 50}
    vector = {"vec": hybrid.vector_literal(axis(0)), "recipe": RECIPE, "n": 50}
    for statement, params in (
        (listing._SELECT, {"c": c, "d": document}),
        (page, {"c": c, "status": None, "pattern": None}),
        (hybrid._MEANING.text, {"c": c, **vector, **EVERYTHING.params()}),
        (hybrid._WORD.text, {"c": c, **words, **EVERYTHING.params()}),
    ):
        nodes = _nodes(_plan(db, c, first, statement, params))
        given = [
            n for n in nodes if n.get("Function Name") in ("kaagaz_shared_with_me", "kaagaz_lock_open_to_me")
        ]
        given += [
            n for n in nodes if n.get("Parent Relationship") == "SubPlan" and n["Node Type"] == "ProjectSet"
        ]
        assert given and all(n["Actual Loops"] == 0 for n in given)
        access = [n for n in nodes if n.get("Function Name") == "kaagaz_access"]
        assert access and all(n["Actual Rows"] in (0, 1) for n in access)


# The SQL that names a view, by file and constant (or function, for SQL written inside one). Each is
# reached from kaagaz/api only (#595, condition 1: no worker statement reads a view; the workers may
# still run as kaagaz_app with no person on a server, and would see nothing). A new one belongs here
# only once its callers are checked.
VIEW_READERS = {
    # PR 8 (#602): Ask, reached from the API only (the SQL guard's worker test).
    "answering/clarify.py": {"_ANY_KEYS", "_CANDIDATES", "_NEAR", "_PICKED_PAGE"},
    "answering/exhaustive.py": {"_IN_SCOPE", "_PIECES"},
    "answering/fixed_queries.py": {
        "_AVAILABLE_FIELDS", "_COUNT_DOCUMENTS", "_COUNT_INVOICES", "_EXTREME_VALUE", "_FIGURES",
        "_TOP_GROUP", "_VALUE_REFS",
    },
    "answering/hard_to_read.py": {"_FLAGGED_PAGES", "_WORD_MARKS"},
    "answering/owners.py": {"_EVERY_RECORD", "_KEYED", "_OWNED", "_RESOLVE", "_SHARES_AS_PRINTED"},
    "answering/unchecked.py": {"_CARD_DOCUMENTS"},
    "db/audit.py": {"_FOLDER_PATHS", "_OF_FAMILY", "_SEEN"},
    "db/chat.py": {"_DOCUMENT_QUESTIONS", "add_pin"},
    "db/document_stories.py": {"_DELETE", "_PUT"},
    "db/ensemble.py": {"_COUNTS", "_LATEST", "_LATEST_READINGS", "_PENDING", "_READING"},
    "db/folder_upload_items.py": {"_PREVIOUS"},
    "db/folders.py": {"_LIST", "_SUMMARY_SEEN"},
    "db/listing.py": {
        "_BATCH", "_CLEAR_COPY_MARKS", "_COLUMNS", "_DASHBOARD_DEPARTMENTS", "_DASHBOARD_RECENT",
        "_DASHBOARD_TILES", "_DASHBOARD_TYPES", "_DOCUMENTS_SEEN", "_FILE_COLUMNS", "_FOLDERS_SEEN",
        "_HAS_NEWER", "_INVOICE", "_INVOICE_LINES", "_LAST_UPLOAD", "_LAST_UPLOADER", "_LINKS", "_LIST",
        "_LOOSE_IN_TRASH", "_OUTSIDE_DEPARTMENTS", "_PAGE_MARKS", "_PAGE_READINGS", "_SELECT",
        "_TRASH_LIST", "_VERSIONS", "_WORD_LINES", "document_facts", "get_page", "list_pages",
        "list_values", "reviewer_fix_count", "sheet_cells",
    },
    "db/organization_words.py": {"decide", "visible"},
    "db/review.py": {"_COUNT", "_COUNT_FOR_DOCUMENT", "_ITEM", "_LOCK_VALUE", "_NEXT_CHUNK"},
    "db/tags.py": {"_FOR_DOCUMENTS"},
    "db/trashed_folders.py": {
        "_CHILD_ITEMS", "_EXISTS", "_HELD", "_ONE_ITEM", "_PATHS", "_SEEN", "_SEEN_RESTORE", "_TOP_ITEMS",
    },
    "db/user_words.py": {"_SPOTS"},
    "db/versions.py": {"_ALLOWED", "_CANDIDATE", "_FOR_DOCUMENT", "_IN_BATCH", "_LIVE", "_VIEW"},
    "db/visibility.py": {"LIVE_SUGGESTION", "SEEN_DOCUMENT"},
    "db/word_review.py": {"_PAGES", "_PIECES_BY_ID", "_VALUES_BY_ID", "_WORDS"},
    # PR 8 (#602): Ask and search, reached from the API only (the SQL guard's worker test).
    "search/hybrid.py": {
        "_CARDS", "_DETAILS", "_FUZZY", "_MEANING", "_MEANING_EXACT", "_NEIGHBOURS", "_SECTION_PIECES",
        "_TOPIC_PIECES", "_VISIBLE_VECTORS", "_WHOLE", "_WHOLE_SIZE", "_WORD",
    },
    "search/story_search.py": {"_STORIES"},
}  # fmt: skip


# Changed on purpose (#599): the trashed folders' view (0087), and visibility.py's fragments that
# read a view, named in a statement rather than written in it.
_VIEWS = ("visible_documents", "visible_folders", "visible_trashed_folders")
_FRAGMENTS = {"LIVE_SUGGESTION", "SEEN_DOCUMENT"}


def test_only_the_reviewed_statements_read_a_view() -> None:
    root = Path(kaagaz.__file__).parent
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            names = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names = [node.name]
            texts = [
                n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            named = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if names and (any(view in t for t in texts for view in _VIEWS) or named & _FRAGMENTS):
                found.setdefault(path.relative_to(root).as_posix(), set()).update(names)
    assert found == VIEW_READERS


# --- lists and counts ---------------------------------------------------------------------


def _ids(reply: Any) -> set[str]:
    return {item["id"] for item in reply.json()["items"]}


def test_hidden_files_are_missing_from_the_list_its_count_and_its_filters(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    for name, signed in org.people.items():
        if name == "off":
            continue
        seen = _expected(db, org, signed.user_id)
        live = seen - {org.files["trashed"]}
        reply = signed.client.get("/api/documents?limit=100")
        assert reply.status_code == 200, (name, reply.text)
        assert (_ids(reply), reply.json()["total"]) == (live, len(live)), name
        for query in (
            "q=fin",
            "detected_type=pdf",
            f"folder_id={org.folders['sales']}",
            "department_id=none",
        ):
            found = _ids(signed.client.get(f"/api/documents?limit=100&{query}"))
            assert found <= live, (name, query)
    plain = org.people["plain"].client
    assert _ids(plain.get("/api/documents?q=fin")) == set()
    assert plain.get(f"/api/documents?folder_id={org.folders['finance']}").status_code == 404


def test_hidden_files_are_missing_from_the_dashboard(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    with db.customer(org.c) as conn:
        for name in ("fin", "sales"):
            audit.record(
                conn,
                org.c,
                actor_user_id=org.people["admin"].user_id,
                action="document.upload",
                target_type="document",
                target_id=org.files[name],
            )
    for name, signed in org.people.items():
        if name == "off":
            continue
        live = _expected(db, org, signed.user_id) - {org.files["trashed"]}
        board = signed.client.get("/api/dashboard").json()
        assert board["tiles"]["documents"] == len(live), name
        assert board["tiles"]["documents_this_week"] == len(live), name  # every file is new this week
        assert sum(row["count"] for row in board["by_type"]) == len(live), name
        assert {row["id"] for row in board["recent"]} <= live, name
        departments = {row["id"] for row in board["departments"]}
        visible = _expected(db, org, signed.user_id, folders=True)
        assert departments == {org.folders["finance"], org.folders["sales"]} & visible, name
        listed = signed.client.get("/api/documents?limit=100&department_id=none").json()["total"]
        assert board["not_in_department"] == listed, name
        about = {item.get("target_id") for item in board["activity"]}
        assert (org.files["fin"] in about) == (org.files["fin"] in live), name
        assert org.files["sales"] in about, name


def test_a_file_given_directly_shows_and_nothing_else_of_its_folder(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    plain = org.people["plain"].client
    reply = plain.get(f"/api/documents/{org.files['given']}")
    assert reply.status_code == 200
    detail = reply.json()
    assert (detail["department"], detail["folder"]) == (None, None)
    listed = {i["id"]: i for i in plain.get("/api/documents?limit=100").json()["items"]}
    assert listed[org.files["given"]]["department"] is None
    assert "Finance" not in json.dumps(detail) + json.dumps(listed[org.files["given"]])
    member = org.people["member"].client.get(f"/api/documents/{org.files['given']}").json()
    assert member["department"]["name"] == member["folder"]["name"] == "Finance"  # who can see it, still does
    # A person named on Salaries, inside Finance which they can't see: no path rather than a part.
    named = org.people["named"].client.get(f"/api/documents/{org.files['sal']}").json()
    assert (named["department"], named["folder"]) == (None, None)


# --- opening by id ------------------------------------------------------------------------

_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/api/documents/{d}", None),
    ("GET", "/api/documents/{d}/summary", None),
    ("GET", "/api/documents/{d}/activity", None),
    ("GET", "/api/documents/{d}/questions", None),
    ("GET", "/api/documents/{d}/file", None),
    ("GET", "/api/documents/{d}/file?download=true", None),
    ("GET", "/api/documents/{d}/pages/1/image", None),
    ("GET", "/api/documents/{d}/pages/1/readings", None),
    ("GET", "/api/documents/{d}/pages/1/cells", None),
    ("GET", "/api/documents/{d}/words?w=total", None),
    ("GET", "/api/documents/{d}/clear-copy", None),
    ("GET", "/api/documents/{d}/my-story", None),
    ("GET", "/api/documents/{d}/my-words?page=1", None),
    ("GET", "/api/documents/{d}/review-status", None),
    ("GET", "/api/documents/{d}/flags", None),
    ("GET", "/api/documents/{d}/changes", None),
    ("GET", "/api/documents/{d}/ensemble", None),
    ("GET", "/api/documents/{d}/ensemble/estimate?scope=document", None),
    ("GET", "/api/documents/{d}/compare?with={v}", None),
    ("POST", "/api/documents/{d}/reprocess", None),
    ("POST", "/api/documents/{d}/pages/1/read-again", None),
    ("POST", "/api/documents/{d}/pages/1/accept", None),
    ("POST", "/api/documents/{d}/autofile/undo", None),
    ("POST", "/api/documents/{d}/restore", None),
    ("POST", "/api/documents/{d}/version-undo", None),
    ("POST", "/api/documents/{d}/make-current", None),
    ("POST", "/api/documents/{d}/separate", {"name": "apart.pdf"}),
    ("POST", "/api/documents/{d}/separate/undo", {"family_id": "{v}"}),
    ("POST", "/api/documents/{d}/ensemble", {"scope": "document"}),
    ("POST", "/api/documents/{d}/ensemble/{r}/stop", None),
    ("POST", "/api/documents/{d}/ensemble/{r}/retry", None),
    ("POST", "/api/documents/{d}/ensemble/{r}/keep-all", None),
    ("POST", "/api/documents/{d}/ensemble/readings/{r}/keep", None),
    ("POST", "/api/documents/{d}/ensemble/readings/{r}/revert", None),
    ("POST", "/api/documents/{d}/flags/{k}/confirm", {"expected_text": "a"}),
    ("POST", "/api/documents/{d}/flags/{k}/edit", {"text": "b", "expected_text": "a"}),
    ("POST", "/api/documents/{d}/lines/{k}/edit", {"text": "b", "expected_text": "a"}),
    ("POST", "/api/documents/{d}/changes/{r}/undo", None),
    ("PATCH", "/api/documents/{d}", {"display_name": "renamed.pdf"}),
    ("PUT", "/api/documents/{d}/tags", {"tag_ids": []}),
    ("PUT", "/api/documents/{d}/my-story", {"story": "My note", "user_id": "{u}"}),
    ("DELETE", "/api/documents/{d}/my-story?user_id={u}", None),
    ("DELETE", "/api/documents/{d}", None),
]
_KEY = "0" * 64


def _call(signed: Signed, method: str, path: str, body: Any, document: str, other: str) -> tuple[int, Any]:
    fill = {"d": document, "v": other, "r": str(uuid.UUID(int=7)), "k": _KEY, "u": signed.user_id}
    url = path.format(**fill)
    payload = (
        None
        if body is None
        else json.loads(json.dumps(body).replace("{v}", other).replace("{u}", signed.user_id))
    )
    reply = signed.client.request(method, url, json=payload, headers=CSRF)
    try:
        return reply.status_code, reply.json()
    except ValueError:
        return reply.status_code, reply.text


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES, ids=[f"{m} {p}" for m, p, _ in _ROUTES])
def test_every_open_by_id_route_answers_a_hidden_file_as_one_that_never_was(
    db: Database, method: str, path: str, body: Any
) -> None:
    """Not 403, not 409 "in Trash", not "not ready": exactly the reply for a random id, for a
    hidden live file and a hidden file in Trash; and the file is left as it was."""
    api = Api(db)
    org = _walled(api)
    plain = org.people["plain"]
    visible = org.files["sales"]
    for hidden in (org.files["fin"], org.files["trashed"]):
        before = _owner(db, "SELECT row_to_json(d)::text FROM documents d WHERE id = :d", {"d": hidden})
        missing = _call(plain, method, path, body, str(uuid.uuid4()), visible)
        assert missing[0] in (404, 403), missing  # 403: a role check that comes before any lookup
        assert _call(plain, method, path, body, hidden, visible) == missing
        assert (
            _owner(db, "SELECT row_to_json(d)::text FROM documents d WHERE id = :d", {"d": hidden}) == before
        )


def test_a_new_version_upload_onto_a_hidden_file_is_not_found(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"].client
    files = {"file": ("v2.pdf", PDF_BYTES, "application/pdf")}
    for target in (org.files["fin"], str(uuid.uuid4())):
        reply = plain.post(f"/api/documents/{target}/versions", files=files, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), reply.text


def test_most_relevant_answers_the_same_for_a_hidden_and_a_missing_file(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"]
    message = _owner(
        db,
        "WITH c AS (INSERT INTO conversations (customer_id, user_id, title)"
        " VALUES (:c, :u, 'Q') RETURNING id)"
        " INSERT INTO messages (customer_id, conversation_id, role, text, answer)"
        " SELECT :c, id, 'assistant', 'A', '{}'::jsonb FROM c"
        " RETURNING id",
        {"c": org.c, "u": plain.user_id},
    )[0][0]
    replies = [
        plain.client.post(
            f"/api/messages/{message}/pin",
            json={"document_id": d, "page_no": 1, "previous_rank": 2},
            headers=CSRF,
        )
        for d in (org.files["fin"], str(uuid.uuid4()), org.files["sales"])
    ]
    assert [r.status_code for r in replies] == [404, 404, 204]
    assert replies[0].json() == replies[1].json()


def test_a_new_version_suggestion_shows_only_when_both_files_are_visible(db: Database) -> None:
    """Sales (open) is suggested as a new version of Finance's file: Finance's member sees the
    suggestion everywhere; the plain reviewer, who can't see Finance, sees it nowhere."""
    org = _walled(Api(db))
    new, family = org.files["sales"], org.files["fin"]
    suggestion = str(_owner(db, _SUGGEST, {"c": org.c, "d": new, "f": family})[0][0])
    for name, shown in (("member", True), ("plain", False)):
        client = org.people[name].client
        detail = client.get(f"/api/documents/{new}").json()
        assert (detail["version_suggestion"] is not None) == shown, name
        listed = {i["id"]: i for i in client.get("/api/documents?limit=100").json()["items"]}
        assert (listed[new]["pending_suggestion"] is not None) == shown, name
        assert client.get("/api/dashboard").json()["tiles"]["versions_to_confirm"] == int(shown), name
    plain = org.people["plain"].client
    assert _ids(plain.get("/api/documents?has_newer_version=true")) == set()
    assert plain.get(f"/api/documents/{new}/compare?with={family}").status_code == 404
    for action in ("accept", "reject"):
        reply = plain.post(f"/api/version-suggestions/{suggestion}/{action}", headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), action
    assert _owner(db, "SELECT status FROM version_suggestions WHERE id = :s", {"s": suggestion}) == [
        ("pending",)
    ]


def test_versions_links_and_family_columns_show_no_hidden_version(db: Database) -> None:
    """A locked second version the reviewer may not open, and a linked Finance file: the first
    version's detail shows neither, and counts one version with no newer one."""
    api = Api(db)
    org = _walled(api)
    first = org.files["sales"]
    second = api.add_document(
        org.c,
        name="sales v2.pdf",
        folder_id=org.folders["sales"],
        family_id=first,
        version_no=2,
        is_current=False,
        uploaded_by=org.people["admin"].user_id,
    )
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": org.people["admin"].user_id, "d": second},
    )
    _owner(
        db,
        "INSERT INTO document_links"
        " (customer_id, document_id, linked_document_id, identifier_kind, identifier_value)"
        " VALUES (:c, :a, :b, 'invoice_number', 'X'), (:c, :b, :a, 'invoice_number', 'X')",
        {"c": org.c, "a": first, "b": org.files["fin"]},
    )
    _owner(db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": org.files["fin"]})
    for name in ("plain", "management", "admin"):  # a lock nobody was given: R0 hides it from all
        detail = org.people[name].client.get(f"/api/documents/{first}").json()
        assert [v["id"] for v in detail["versions"]] == [first], name
        assert detail["newer_version"] is None, name
        listed = {i["id"]: i for i in org.people[name].client.get("/api/documents?limit=100").json()["items"]}
        assert (listed[first]["newer_version"], listed[first]["versions"]) == (None, 1), name
        assert org.people[name].client.get(f"/api/documents/{second}").status_code == 404, name
    plain = org.people["plain"].client.get(f"/api/documents/{first}").json()
    assert [link["id"] for link in plain["links"]] == []
    member = org.people["member"].client.get(f"/api/documents/{first}").json()
    assert [link["id"] for link in member["links"]] == [org.files["fin"]]


def test_new_versions_this_month_count_only_versions_the_person_sees(db: Database) -> None:
    """A second version of Sales's file, and a locked second version nobody was given."""
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"].user_id
    for name, place in (("sales", "sales"), ("loose", "loose")):
        api.add_document(
            org.c, name=f"{name} v2.pdf", folder_id=org.folders[place], family_id=org.files[name],
            version_no=2, is_current=False, uploaded_by=admin,
        )  # fmt: skip
    hidden = _owner(
        db, "SELECT id FROM documents WHERE family_id = :f AND version_no = 2", {"f": org.files["loose"]}
    )
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": admin, "d": hidden[0][0]},
    )
    for name in ("plain", "member", "management", "admin"):
        board = org.people[name].client.get("/api/dashboard").json()
        assert board["tiles"]["new_versions_this_month"] == 1, name


def test_a_closed_department_in_trash_stays_closed(db: Database) -> None:
    """Finance (Department only) goes to Trash with its files: the plain reviewer still can't see
    its files, its member still can, and every person still sees exactly the written rule."""
    org = _walled(Api(db))
    reply = org.people["admin"].client.delete(
        f"/api/folders/{org.folders['finance']}?contents=trash", headers=CSRF
    )
    assert reply.status_code == 200, reply.text
    app = _app(db)
    assert org.files["fin"] not in _seen(app, org.c, org.people["plain"].user_id)
    assert org.files["fin"] in _seen(app, org.c, org.people["member"].user_id)
    for name, signed in org.people.items():
        assert _seen(app, org.c, signed.user_id) == _expected(db, org, signed.user_id), name


def test_a_hidden_folder_filter_answers_as_one_that_never_was(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"].client
    for query, hidden in (("folder_id", org.folders["salaries"]), ("department_id", org.folders["finance"])):
        missing = plain.get(f"/api/documents?{query}={uuid.uuid4()}")
        found = plain.get(f"/api/documents?{query}={hidden}")
        assert missing.status_code == 404, query
        assert (found.status_code, found.json()) == (missing.status_code, missing.json()), query
    member = org.people["member"].client.get(f"/api/documents?department_id={org.folders['finance']}")
    assert member.status_code == 200 and org.files["fin"] in _ids(member)


def test_the_feed_names_no_hidden_folder_and_shows_a_given_files_history(db: Database) -> None:
    """An entry about a folder inside Salaries is left out for the plain reviewer; an entry naming
    Salaries as a parent shows without its path; the file given to them shows its history."""
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"].user_id
    with db.customer(org.c) as conn:
        inside = insert_folder(conn, org.c, admin, "Inside", org.folders["salaries"])
        moved = insert_folder(conn, org.c, admin, "Moved out", org.folders["salaries"])
        for folder in (inside, moved):
            audit.record(
                conn, org.c, actor_user_id=admin, action="folder.create", target_type="folder",
                target_id=folder, details={"parent_id": org.folders["salaries"]},
            )  # fmt: skip
        audit.record(
            conn, org.c, actor_user_id=admin, action="document.upload", target_type="document",
            target_id=org.files["given"],
        )  # fmt: skip
    _owner(db, "UPDATE folders SET parent_id = NULL WHERE id = :f", {"f": moved})  # moved out later
    plain = org.people["plain"].client
    feed = {item["target_id"]: item for item in plain.get("/api/dashboard").json()["activity"]}
    assert inside not in feed and moved in feed
    assert "Salaries" not in json.dumps(feed[moved]) and "Finance" not in json.dumps(feed[moved])
    named = {i["target_id"]: i for i in org.people["named"].client.get("/api/dashboard").json()["activity"]}
    assert inside in named and "Salaries" in json.dumps(named[moved])
    history = plain.get(f"/api/documents/{org.files['given']}/activity").json()["items"]
    assert [item["target_id"] for item in history] == [org.files["given"]]


def test_join_again_needs_the_family_it_rejoins_to_be_visible(db: Database) -> None:
    """D was made a separate file from F; F is now held for the member and locked. The plain
    reviewer, who sees D but not F, can't join them again: 404, and nothing changes. The member,
    given F's lock, can."""
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    family = api.add_document(
        org.c, name="deed.pdf", folder_id=org.folders["sales"], uploaded_by=admin.user_id, is_current=False
    )
    later = api.add_document(
        org.c, name="deed v2.pdf", folder_id=org.folders["sales"], uploaded_by=admin.user_id,
        family_id=family, version_no=2,
    )  # fmt: skip
    reply = admin.client.post(
        f"/api/documents/{later}/separate", json={"name": "deed apart.pdf"}, headers=CSRF
    )
    assert reply.status_code == 200, reply.text
    member = org.people["member"].user_id
    _owner(
        db,
        "UPDATE documents SET held_for = :m, locked_at = now(), locked_by = :a WHERE family_id = :f",
        {"m": member, "a": admin.user_id, "f": family},
    )
    _owner(
        db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :m, :a)",
        {"c": org.c, "d": family, "m": member, "a": admin.user_id},
    )
    state = "SELECT row_to_json(d)::text FROM documents d WHERE family_id = :f OR id = :l ORDER BY id"
    tags = "SELECT tag_id FROM document_tags WHERE document_id = :l"
    before = (_owner(db, state, {"f": family, "l": later}), _owner(db, tags, {"l": later}))
    body = {"family_id": family}
    plain = org.people["plain"].client.post(f"/api/documents/{later}/separate/undo", json=body, headers=CSRF)
    assert (plain.status_code, plain.json()["error"]["code"]) == (404, "not_found")
    assert (_owner(db, state, {"f": family, "l": later}), _owner(db, tags, {"l": later})) == before
    joined = org.people["member"].client.post(
        f"/api/documents/{later}/separate/undo", json=body, headers=CSRF
    )
    assert joined.status_code == 200, joined.text


def test_deleting_a_hidden_file_forever_is_not_found_and_deletes_nothing(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"]
    assert _grant(org.people["admin"], plain.user_id) == (200, True)
    assert _confirm(plain) == 204
    hidden = org.files["trashed"]  # in Trash, in Salaries
    missing = plain.client.delete(f"/api/documents/{uuid.uuid4()}/permanent", headers=CSRF)
    reply = plain.client.delete(f"/api/documents/{hidden}/permanent", headers=CSRF)
    assert missing.status_code == 404
    assert (reply.status_code, reply.json()) == (missing.status_code, missing.json())
    assert _owner(db, "SELECT count(*) FROM documents WHERE id = :d", {"d": hidden}) == [(1,)]


def test_no_module_outside_the_api_calls_a_function_that_reads_a_view() -> None:
    """Condition 1 on #595, kept by a scan: the functions of VIEW_READERS' modules that use one of
    its statements (or call one that does) are called only from kaagaz/api and search/scope.py."""
    root = Path(kaagaz.__file__).parent
    readers: dict[str, set[str]] = {}
    for file, names in VIEW_READERS.items():
        tree, tainted = ast.parse((root / file).read_text(encoding="utf-8")), set(names) | _FRAGMENTS
        for _ in range(len(tree.body)):  # until nothing new is tainted
            for node in tree.body:
                name = getattr(node, "name", None) or next(
                    (t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)), None
                )
                if name and any(isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(node)):
                    tainted.add(name)
        readers[Path(file).stem] = {
            n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in tainted
        }
    calls = []
    for path in sorted(root.rglob("*.py")):
        where = path.relative_to(root).as_posix()
        if where.startswith("api/") or where == "search/scope.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {
            a.asname or a.name: a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "kaagaz.db"
            for a in n.names
        }
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("kaagaz.db."):
                module = (n.module or "").split(".")[-1]
                calls += [f"{where}: {a.name}" for a in n.names if a.name in readers.get(module, ())]
            module = (
                aliases.get(n.value.id, "")
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                else ""
            )
            if isinstance(n, ast.Attribute) and n.attr in readers.get(module, ()):
                calls.append(f"{where}: {module}.{n.attr}")
    assert calls == []
