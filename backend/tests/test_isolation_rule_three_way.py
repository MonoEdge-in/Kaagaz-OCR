"""PRD 7 PR 9a (#606, p3a:67): the same people and files through the rule three ways, and a fourth:
the plain reference written in the test (``_expected``), the access views, the database rule (an
unfiltered read as the API role) and PR 10a's ``kaagaz_audience(...).sees``. Each seeded
organisation is built at random (seeds fixed, no new dependency): departments open and Department
only, members and heads, private folders and their named people, files given, held, locked (with
and without a grant), in Trash, several versions, a department deleted for good after its files
went to Trash, a person switched off, Management and Admins. Every disagreement is listed.

The fast path: whenever ``kaagaz_sees_everything()`` is true for a person, they see every file. An
organisation that never walled anything is run again as walled (``walls_set``), where both are
consistent (every department still open)."""

import random
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import APP_ROLE, insert_document, new_customer, rule_on
from tests.test_api_support import Api
from tests.test_isolation_access_views import Org, _all, _expected
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _the_rule_on(db: Database) -> Iterator[None]:
    """A test of the rule itself: on for every organisation, in a baseline session too (after the
    session's database is migrated)."""
    with rule_on():
        yield


SEEDS = range(8)


def _build(db: Database, seed: int) -> tuple[Org, list[str]]:
    """One random organisation (module notes): (it, its people's ids, the admin first)."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, for the shape of a test organisation
    api = Api(db)
    c, admin = new_customer(db, name=f"Three way {seed}")
    people = [admin] + [api.add_user(c, rng.choice(["member", "reviewer"]))[0] for _ in range(5)]
    people.append(api.add_user(c, "admin")[0])
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": people[1]})

    def run(statement: str, **params: Any) -> None:
        _owner(db, statement, {"c": c, **params})

    departments: list[str] = []
    places: list[str | None] = [None]
    with db.customer(c) as conn:
        for n in range(3):
            d = str(
                conn.execute(
                    text(
                        "INSERT INTO folders (customer_id, name, created_by) VALUES (:c, :n, :u) RETURNING id"
                    ),
                    {"c": c, "n": f"Dept {n}", "u": admin},
                ).scalar_one()
            )
            departments.append(d)
    for d in departments:
        run(
            "UPDATE folders SET kind = 'department', department_only = :o WHERE id = :d",
            d=d,
            o=rng.random() < 0.5,
        )
        for user in rng.sample(people[2:6], rng.randint(0, 3)):
            run(
                "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
                " VALUES (:c, :d, :u, :h, :by)",
                d=d,
                u=user,
                h=rng.random() < 0.3,
                by=admin,
            )
        with db.customer(c) as conn:
            inner = str(
                conn.execute(
                    text(
                        "INSERT INTO folders (customer_id, parent_id, name, created_by)"
                        " VALUES (:c, :p, 'In', :u) RETURNING id"
                    ),
                    {"c": c, "p": d, "u": admin},
                ).scalar_one()
            )
        places += [d, inner]
        if rng.random() < 0.6:
            run("UPDATE folders SET private = true WHERE id = :f", f=inner)
            for user in rng.sample(people[2:6], rng.randint(0, 2)):
                run(
                    "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
                    " VALUES (:c, :f, :u, :by)",
                    f=inner,
                    u=user,
                    by=admin,
                )
    files: list[str] = []
    for _ in range(10):
        with db.customer(c) as conn:
            first = insert_document(conn, c, folder_id=rng.choice(places), uploaded_by=admin)
            files.append(first)
            if rng.random() < 0.3:  # a second version, in the family's folder
                folder = conn.execute(
                    text("SELECT folder_id FROM documents WHERE id = :d"), {"d": first}
                ).scalar()
                files.append(
                    insert_document(
                        conn,
                        c,
                        family_id=first,
                        version_no=2,
                        is_current=False,
                        folder_id=folder,
                        uploaded_by=admin,
                    )
                )
    for document in files:
        roll = rng.random()
        if roll < 0.15:
            run(
                "UPDATE documents SET held_for = :u, folder_id = NULL WHERE id = :d",
                u=rng.choice(people),
                d=document,
            )
        elif roll < 0.3:
            run("UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d", u=admin, d=document)
            if rng.random() < 0.5:
                run(
                    "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by)"
                    " VALUES (:c, :d, :u, :u)",
                    d=document,
                    u=rng.choice(people),
                )
        if rng.random() < 0.2:
            run(
                "INSERT INTO document_shares (customer_id, document_id, user_id, given_by)"
                " VALUES (:c, :d, :u, :by) ON CONFLICT DO NOTHING",
                d=document,
                u=rng.choice(people[2:]),
                by=admin,
            )
        if rng.random() < 0.15:
            run(
                "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE family_id ="
                " (SELECT family_id FROM documents WHERE id = :d)",
                u=admin,
                d=document,
            )
    if rng.random() < 0.4:  # a department deleted for good after its files went to Trash
        gone = rng.choice(departments)
        run(
            "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE folder_id IN"
            " (SELECT id FROM folders WHERE id = :g OR parent_id = :g)",
            u=admin,
            g=gone,
        )
        run(
            "UPDATE documents SET folder_id = NULL WHERE deleted_at IS NOT NULL AND folder_id IN"
            " (SELECT id FROM folders WHERE id = :g OR parent_id = :g)",
            g=gone,
        )
        run(
            "DELETE FROM private_folder_members"
            " WHERE folder_id IN (SELECT id FROM folders WHERE parent_id = :g)",
            g=gone,
        )
        run("DELETE FROM folders WHERE parent_id = :g", g=gone)
        run("DELETE FROM department_members WHERE department_id = :g", g=gone)
        run("DELETE FROM folders WHERE id = :g", g=gone)
    _owner(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": people[5]})
    return Org(c, {}, {}, {}), people


def _ids(db: Database, c: str, user: str, statement: str, **params: Any) -> set[str]:
    with Database(db.engine, APP_ROLE).user(c, user) as conn:
        return {str(row[0]) for row in conn.execute(text(statement), params)}


def _audience(db: Database, c: str, admin: str, kind: str, ids: set[str]) -> dict[str, set[str]]:
    """For each person, the ids kaagaz_audience says they see (asked as an Admin)."""
    seen: dict[str, set[str]] = {}
    with Database(db.engine, APP_ROLE).user(c, admin) as conn:
        for target in ids:
            for user, sees in conn.execute(
                text("SELECT user_id, sees FROM kaagaz_audience(:k, :t)"), {"k": kind, "t": target}
            ):
                if sees:
                    seen.setdefault(str(user), set()).add(target)
    return seen


def _disagreements(db: Database, org: Org, people: list[str]) -> list[str]:
    c = org.c
    files, folders = _all(db, c), _all(db, c, "folders")
    by_audience = _audience(db, c, people[0], "document", files)
    folder_audience = _audience(db, c, people[0], "folder", folders)
    said = []
    for user in people:
        legs = {
            "reference": _expected(db, org, user),
            "view": _ids(db, c, user, "SELECT id FROM visible_documents"),
            "rule": _ids(db, c, user, "SELECT id FROM documents"),
            "audience": by_audience.get(user, set()),
        }
        folder_legs = {
            "reference": _expected(db, org, user, folders=True),
            "view": _ids(db, c, user, "SELECT id FROM visible_folders"),
            "rule": _ids(db, c, user, "SELECT id FROM folders"),
            "audience": folder_audience.get(user, set()),
        }
        trashed = (
            _ids(db, c, user, "SELECT id FROM visible_trashed_folders"),
            _ids(db, c, user, "SELECT id FROM trashed_folders"),
        )
        for kind, found in (("documents", legs), ("folders", folder_legs)):
            for leg, ids in found.items():
                if ids != found["reference"]:
                    said.append(f"{c} {user} {kind}: {leg} differs by {sorted(ids ^ found['reference'])}")
        if trashed[0] != trashed[1]:
            said.append(f"{c} {user} trashed folders: rule differs by {sorted(trashed[0] ^ trashed[1])}")
        fast = _ids(db, c, user, "SELECT 'yes' WHERE kaagaz_sees_everything()")
        if fast and legs["reference"] != files:
            said.append(
                f"{c} {user}: the fast path is true but {len(files - legs['reference'])} files are hidden"
            )
    return said


@pytest.mark.parametrize("seed", SEEDS)
def test_the_reference_the_views_the_rule_and_the_audience_agree(db: Database, seed: int) -> None:
    org, people = _build(db, seed)
    said = _disagreements(db, org, people)
    walled = bool(_owner(db, "SELECT walls_set FROM customers WHERE id = :c", {"c": org.c})[0][0])
    if not walled:  # consistent both ways: every department is open, so walled changes nobody's files
        _owner(db, "UPDATE customers SET walls_set = true WHERE id = :c", {"c": org.c})
        said += [f"walled: {line}" for line in _disagreements(db, org, people)]
    assert said == []


def test_the_seeds_cover_every_case(db: Database) -> None:
    """Across the seeds: walled and not, private, held, locked with and without a grant, given, in
    Trash, versions, and a department deleted for good."""
    cases = {
        "walled": "SELECT bool_or(walls_set) FROM customers WHERE name LIKE 'Three way %'",
        "private": "SELECT count(*) > 0 FROM documents d JOIN customers c ON c.id = d.customer_id"
        " WHERE c.name LIKE 'Three way %' AND d.private_folder_id IS NOT NULL",
        "held": "SELECT count(*) > 0 FROM documents d JOIN customers c ON c.id = d.customer_id"
        " WHERE c.name LIKE 'Three way %' AND d.held_for IS NOT NULL",
        "locked": "SELECT count(*) > 0 FROM documents d JOIN customers c ON c.id = d.customer_id"
        " WHERE c.name LIKE 'Three way %' AND d.locked_at IS NOT NULL",
        "versions": "SELECT count(*) > 0 FROM documents d JOIN customers c ON c.id = d.customer_id"
        " WHERE c.name LIKE 'Three way %' AND d.version_no > 1",
        "trash": "SELECT count(*) > 0 FROM documents d JOIN customers c ON c.id = d.customer_id"
        " WHERE c.name LIKE 'Three way %' AND d.deleted_at IS NOT NULL AND d.folder_id IS NULL"
        " AND d.department_id IS NOT NULL",
    }
    for seed in SEEDS:
        _build(db, seed)
    for case, statement in cases.items():
        assert _owner(db, statement, {})[0][0], case
