"""File management (Phase 2 PRD 1) stage 1 against PostgreSQL: migration 0041 up, down and
up; the new columns and version_suggestions kept to their customer; department folders
(F1); a department at upload and the department template (F2, F3, section 10); old folders
stay custom (F4); owner, label, stamps and the note (O1 to O4); the dashboard (D2, two
customers and 10,000 documents); the list's filters and sorting (Q3, Q4); and Trash (T1).
"""

import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import listing
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import _TYPE_LABEL, CONFIDENTIAL_TYPES
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, insert_folder, migrate, new_customer
from tests.test_api_support import CSRF, Api, MovingClock, Signed
from tests.test_autofile_db import file, organisation, upload
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_CARD = "UPDATE documents SET doc_type = :t, card = CAST(:card AS jsonb) WHERE customer_id = :c AND id = :d"
_PLACE = (
    "SELECT f.name, f.kind, f.autofile_key, p.name, g.name FROM documents d"
    " JOIN folders f ON f.customer_id = d.customer_id AND f.id = d.folder_id"
    " LEFT JOIN folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id"
    " LEFT JOIN folders g ON g.customer_id = p.customer_id AND g.id = p.parent_id"
    " WHERE d.customer_id = :c AND d.id = :d"
)
_SUGGEST = (
    "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, text_overlap,"
    " name_similarity, outcome, status) VALUES (:c, :d, :f, 0.86, 0.31, 'suggested', 'pending')"
)


# --- the migration ------------------------------------------------------------------------


def test_0041_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0040")
    at_0040 = m0002._schema(database)
    migrate(url, "head")
    assert _current(url) == _head() == "0041"
    at_head = m0002._schema(database)
    assert "version_suggestions" in m0002._names(at_head, "relations")
    m0002._downgrade(url, "0040")
    assert m0002._schema(database) == at_0040
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_0041_backfills_owner_label_and_name_key_and_keeps_old_folders_custom(
    fresh: tuple[str, Database],
) -> None:
    """F4 and the backfills: the owner is the family's first uploader, a land record's family
    starts Confidential, every row gets its name key, and every folder stays custom."""
    url, database = fresh
    migrate(url, "0040")
    customer, admin = new_customer(database)
    with database.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Old folder")
        first = insert_document(
            conn,
            customer,
            display_name="7/12 Survey 44 Wakad v2 (1).pdf",
            uploaded_by=admin,
            is_current=False,
        )
        insert_document(
            conn, customer, display_name="x.pdf", family_id=first, version_no=2, doc_type="land_record"
        )
    migrate(url, "head")
    with database.customer(customer) as conn:
        rows = conn.execute(
            text(
                "SELECT owner_id::text, sensitivity, name_key FROM documents"
                " WHERE customer_id = :c ORDER BY version_no"
            ),
            {"c": customer},
        ).all()
        kind: object = conn.execute(
            text("SELECT kind FROM folders WHERE id = :f"), {"f": folder}
        ).scalar_one()
    assert [tuple(row) for row in rows] == [
        (admin, "confidential", "7 12 survey 44 wakad"),
        (admin, "confidential", "x"),
    ]
    assert kind == "custom"


def test_version_suggestions_and_the_new_columns_stay_with_their_customer(db: Database) -> None:
    mine, me = new_customer(db)
    theirs, them = new_customer(db)
    with db.customer(theirs) as conn:
        document = insert_document(conn, theirs, uploaded_by=them, owner_id=them, sensitivity="restricted")
        conn.execute(text(_SUGGEST), {"c": theirs, "d": document, "f": str(uuid.uuid4())})
    with db.customer(mine) as conn:
        assert conn.execute(text("SELECT count(*) FROM version_suggestions")).scalar_one() == 0
        assert (
            conn.execute(text("SELECT count(*) FROM documents WHERE sensitivity = 'restricted'")).scalar_one()
            == 0
        )
    with pytest.raises(DBAPIError), db.customer(mine) as conn:  # never written as another customer
        conn.execute(text(_SUGGEST), {"c": theirs, "d": document, "f": str(uuid.uuid4())})
    with pytest.raises(DBAPIError), db.customer(mine) as conn:  # an owner from another customer
        insert_document(conn, mine, uploaded_by=me, owner_id=them)
    with db.customer(theirs) as conn:
        assert conn.execute(text("SELECT count(*) FROM version_suggestions")).scalar_one() == 1


# --- F1: department folders ---------------------------------------------------------------


def test_f1_only_an_admin_makes_a_department_and_only_at_the_top_level(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    body = {"name": "Land & Legal", "kind": "department", "sort_order": 1}
    assert member.client.post("/api/folders", json=body, headers=CSRF).status_code == 403
    made = admin.client.post("/api/folders", json=body, headers=CSRF)
    assert made.status_code == 201
    department = made.json()
    assert (department["kind"], department["sort_order"]) == ("department", 1)
    nested = {"name": "Inner", "kind": "department", "parent_id": department["id"]}
    assert admin.client.post("/api/folders", json=nested, headers=CSRF).json()["error"]["code"] == (
        "department_top_level"
    )
    custom = member.client.post(
        "/api/folders", json={"name": "Mine", "parent_id": department["id"]}, headers=CSRF
    )
    assert custom.json()["kind"] == "custom"  # anyone makes a custom folder anywhere
    path = f"/api/folders/{department['id']}"
    assert member.client.patch(path, json={"name": "Land"}, headers=CSRF).status_code == 403
    elsewhere = admin.client.post("/api/folders", json={"name": "Elsewhere"}, headers=CSRF).json()
    moved = admin.client.patch(path, json={"parent_id": elsewhere["id"]}, headers=CSRF)
    assert (moved.status_code, moved.json()["error"]["code"]) == (409, "department_top_level")
    ordered = admin.client.patch(path, json={"sort_order": 3}, headers=CSRF).json()
    assert (ordered["kind"], ordered["sort_order"]) == ("department", 3)
    assert (
        member.client.patch(
            f"/api/folders/{custom.json()['id']}", json={"kind": "department"}, headers=CSRF
        ).status_code
        == 403
    )
    assert "folder.kind" in api.audit_actions(admin.customer_id)


# --- F2, F3: a department at upload and its template -------------------------------------


def _department(db: Database, customer: str, admin: str, name: str = "Land & Legal") -> str:
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, name)
        conn.execute(text("UPDATE folders SET kind = 'department' WHERE id = :f"), {"f": folder})
    return folder


def _read(db: Database, customer: str, document: str, doc_type: str, card: dict[str, Any]) -> str | None:
    with db.customer(customer) as conn:
        conn.execute(text(_CARD), {"c": customer, "d": document, "t": doc_type, "card": json.dumps(card)})
    PgDocuments(db).mark_ready(customer, document, 1)
    return file(db, customer, document)


def _place(db: Database, customer: str, document: str) -> tuple[Any, ...]:
    with db.customer(customer) as conn:
        return tuple(conn.execute(text(_PLACE), {"c": customer, "d": document}).one())


def _land(survey: str | None, place: str | None = "Wakad") -> dict[str, Any]:
    return {
        "identifiers": [] if survey is None else [{"kind": "survey_number", "value": survey}],
        "key_facts": [] if place is None else [{"field": "village", "value": place}],
    }


def test_f2_f3_land_and_its_deed_file_by_property_and_44_slash_2_stays_apart(db: Database) -> None:
    customer, admin = organisation(db)
    department = _department(db, customer, admin)
    satbara = upload(db, customer, admin, folder=department)  # F2: auto-filing still runs in a department
    assert _read(db, customer, satbara, "land_record", _land("४४")) == "filed"
    place = _place(db, customer, satbara)
    assert place[:2] == ("Survey 44, Wakad", "property")
    assert place[2] == "property:wakad:44"
    assert place[3:] == ("Land records", "Land & Legal")
    deed = upload(db, customer, admin, folder=department)
    assert _read(db, customer, deed, "contract", _land("44", None)) == "filed"
    assert _place(db, customer, deed)[0] == "Survey 44, Wakad"  # found again by its key
    split = upload(db, customer, admin, folder=department)
    _read(db, customer, split, "land_record", _land("स.नं. ४४/२"))
    assert _place(db, customer, split)[:3] == ("Survey 44/2, Wakad", "property", "property:wakad:44/2")
    nameless = upload(db, customer, admin, folder=department)
    _read(db, customer, nameless, "land_record", _land("12", None))
    assert _place(db, customer, nameless)[0] == "Survey 12"  # no village


def test_f3_a_land_record_with_no_survey_number_and_others_file_by_year(db: Database) -> None:
    customer, admin = organisation(db)
    department = _department(db, customer, admin, "Accounts")
    land = upload(db, customer, admin, folder=department)
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET document_date = '2025-06-12' WHERE id = :d"), {"d": land})
    _read(db, customer, land, "land_record", _land(None))
    assert _place(db, customer, land)[:2] == ("2025", "year")
    assert _place(db, customer, land)[3:] == ("Land records", "Accounts")
    letter = upload(db, customer, admin, folder=department)
    _read(db, customer, letter, "letter", {})
    assert _place(db, customer, letter)[3] == "Letters"
    with db.customer(customer) as conn:
        kinds: dict[Any, Any] = dict(
            conn.execute(text("SELECT name, kind FROM folders WHERE customer_id = :c"), {"c": customer}).all()
        )
    assert kinds["Land records"] == kinds["Letters"] == "category"


def test_f3_a_file_with_no_department_is_filed_as_before(db: Database) -> None:
    customer, admin = organisation(db)
    document = upload(db, customer, admin)
    _read(db, customer, document, "land_record", _land("44"))
    name, kind, key, parent, _ = _place(db, customer, document)
    assert (name, kind, key, parent) == ("Land records", "category", "type:land_record", None)


def test_f2_a_department_form_field_must_be_a_department(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    custom = api.add_folder(signed.customer_id, signed.user_id, "Custom")
    files = {"file": ("a.pdf", b"%PDF-1.7\n" + b"1" * 200, "application/pdf")}
    reply = signed.client.post("/api/documents", data={"department_id": custom}, files=files, headers=CSRF)
    assert reply.status_code == 404
    department = _department(db, signed.customer_id, signed.user_id)
    reply = signed.client.post(
        "/api/documents", data={"department_id": department}, files=files, headers=CSRF
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["document"]["folder_id"] == department


# --- O1 to O4: owner, label, stamps and the note -------------------------------------------


def _family(api: Api, signed: Signed) -> tuple[str, str]:
    first = api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, is_current=False, owner_id=signed.user_id
    )
    second = api.add_document(
        signed.customer_id,
        uploaded_by=signed.user_id,
        name="v2.pdf",
        family_id=first,
        version_no=2,
        owner_id=signed.user_id,
    )
    return first, second


def test_o1_to_o4_owner_label_and_note_through_patch(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    first, second = _family(api, signed)
    anita, _ = api.add_user(signed.customer_id, "member")
    other = api.new_signed()
    path = f"/api/documents/{second}"
    assert signed.client.patch(path, json={"owner_id": other.user_id}, headers=CSRF).status_code == 400
    reply = signed.client.patch(
        path,
        json={"owner_id": anita, "sensitivity": "restricted", "version_note": " Mutation  1234 added "},
        headers=CSRF,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["owner"]["id"], body["sensitivity"], body["version_note"]) == (
        anita,
        "restricted",
        "Mutation 1234 added",
    )
    assert body["updated_by"] == "Test admin"  # the details stamp is newer than every upload
    with db.customer(signed.customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT id::text, owner_id::text, sensitivity, sensitivity_set_by_person, version_note,"
                " details_updated_by::text FROM documents WHERE customer_id = :c ORDER BY version_no"
            ),
            {"c": signed.customer_id},
        ).all()
    assert [tuple(row)[1:4] for row in rows] == [(anita, "restricted", True)] * 2  # the whole family
    assert [row[4] for row in rows] == [None, "Mutation 1234 added"]  # the note is this version's
    assert {row[5] for row in rows} == {signed.user_id}
    actions = api.audit_rows(signed.customer_id)
    assert ("document.owner_change", "document", second, {"from": signed.user_id, "to": anita}) in actions
    assert ("document.label_change", "document", second, {"from": "internal", "to": "restricted"}) in actions
    too_long = signed.client.patch(path, json={"version_note": "x" * 201}, headers=CSRF)
    assert too_long.status_code == 400
    assert first


def test_o2_the_type_rule_never_changes_a_label_a_person_set(db: Database) -> None:
    """Section 10: a person set a label, then the document is read again: the label stays."""
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        land = insert_document(conn, customer, uploaded_by=admin)
        chosen = insert_document(
            conn, customer, uploaded_by=admin, sensitivity="public", sensitivity_set_by_person=True
        )
        for document in (land, chosen):
            conn.execute(
                _TYPE_LABEL,
                {"c": customer, "d": document, "doc_type": "land_record", "secret": list(CONFIDENTIAL_TYPES)},
            )
        labels: dict[Any, Any] = dict(
            conn.execute(
                text("SELECT id::text, sensitivity FROM documents WHERE customer_id = :c"), {"c": customer}
            ).all()
        )
        audited: object = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE customer_id = :c"), {"c": customer}
        ).scalar_one()
    assert labels == {land: "confidential", chosen: "public"}
    assert audited == 0  # a label set by the type rule writes nothing


def test_o3_a_rename_and_a_tag_change_stamp_the_family(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    first, second = _family(api, signed)
    assert (
        signed.client.patch(
            f"/api/documents/{first}", json={"display_name": "new.pdf"}, headers=CSRF
        ).status_code
        == 200
    )
    tag = api.add_tag(signed.customer_id, signed.user_id, "Land")
    assert (
        signed.client.put(f"/api/documents/{second}/tags", json={"tag_ids": [tag]}, headers=CSRF).status_code
        == 200
    )
    with db.customer(signed.customer_id) as conn:
        stamped: object = conn.execute(
            text("SELECT count(*) FROM documents WHERE customer_id = :c AND details_updated_by = :u"),
            {"c": signed.customer_id, "u": signed.user_id},
        ).scalar_one()
    assert stamped == 2


# --- D2: the dashboard ----------------------------------------------------------------------


def test_d2_each_customer_sees_only_its_own_counts(db: Database) -> None:
    api = Api(db, clock=MovingClock(datetime.now(UTC)))  # the rows are stamped by the database's clock
    mine = api.new_admin()
    theirs = api.new_admin()
    department = _department(db, mine.customer_id, mine.user_id)
    _, second = _family(api, mine)
    with db.customer(mine.customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET folder_id = :f, doc_type = 'land_record' WHERE customer_id = :c"),
            {"f": department, "c": mine.customer_id},
        )
        conn.execute(text(_SUGGEST), {"c": mine.customer_id, "d": second, "f": str(uuid.uuid4())})
    api.add_document(mine.customer_id, uploaded_by=mine.user_id, name="loose.pdf")
    for _ in range(3):
        api.add_document(theirs.customer_id, uploaded_by=theirs.user_id)
    _department(db, theirs.customer_id, theirs.user_id, "Theirs")
    body = mine.client.get("/api/dashboard").json()
    assert body["tiles"] == {
        "documents": 2,
        "documents_this_week": 2,
        "departments": 1,
        "new_versions_this_month": 1,
        "versions_to_confirm": 1,
    }
    assert body["departments"] == [{"id": department, "name": "Land & Legal", "sort_order": None, "files": 1}]
    assert body["not_in_department"] == 1
    assert {row["doc_type"]: row["count"] for row in body["by_type"]} == {"land_record": 1, None: 1}
    assert len(body["recent"]) == 2
    other = theirs.client.get("/api/dashboard").json()
    assert other["tiles"]["documents"] == 3 and other["tiles"]["versions_to_confirm"] == 0
    assert [row["name"] for row in other["departments"]] == ["Theirs"]


def test_d2_the_dashboard_answers_in_under_300_ms_on_10000_documents(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
                " status, uploaded_by, owner_id, family_id, doc_type)"
                " SELECT x.id, :c, 'd.pdf', md5(x.id::text) || md5(x.id::text || 'x'), 10, 'pdf', 'ready',"
                " :u, :u,"
                " x.id, (ARRAY['invoice', 'land_record', 'letter'])[1 + n % 3]"
                " FROM (SELECT gen_random_uuid() AS id, n FROM generate_series(1, 10000) AS n) x"
            ),
            {"c": customer, "u": admin},
        )
        conn.execute(text("ANALYZE documents"))
    now = datetime.now(UTC)
    timings = []
    for _ in range(3):
        with db.customer(customer) as conn:
            started = time.perf_counter()
            found = listing.dashboard(conn, customer, week=now - timedelta(days=7), month=now.replace(day=1))
            timings.append(time.perf_counter() - started)
    assert found["tiles"]["documents"] == 10000
    assert min(timings) < 0.3, timings


# --- Q3, Q4: the list's filters and sorting -------------------------------------------------


def _ids(signed: Signed, **params: Any) -> list[str]:
    reply = signed.client.get("/api/documents", params=params)
    assert reply.status_code == 200, reply.text
    return [item["id"] for item in reply.json()["items"]]


def test_q3_q4_the_new_filters_and_sorting(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    anita, _ = api.add_user(signed.customer_id, "member")
    department = _department(db, signed.customer_id, signed.user_id)
    inner = api.add_folder(signed.customer_id, signed.user_id, "Land records", department)
    land = api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, name="b.pdf", owner_id=anita,
        sensitivity="confidential", folder_id=inner, document_date=date(2025, 6, 12),
    )  # fmt: skip
    deed = api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, name="a.pdf", owner_id=signed.user_id,
        sensitivity="restricted", document_date=date(2019, 3, 4),
    )  # fmt: skip
    card = {
        "parties": [{"name": "शिंदे रमेश", "name_latin": "shinde ramesh"}],
        "identifiers": [{"kind": "survey_number", "value": "स.नं. ४४"}],
    }
    with db.customer(signed.customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET card = CAST(:card AS jsonb) WHERE id = :d"),
            {"card": json.dumps(card), "d": deed},
        )
        conn.execute(text(_SUGGEST), {"c": signed.customer_id, "d": deed, "f": land})
    assert _ids(signed, department_id=department) == [land]
    assert _ids(signed, owner_id=anita) == [land]
    assert set(_ids(signed, sensitivity=["restricted", "confidential"])) == {land, deed}
    assert _ids(signed, sensitivity="restricted") == [deed]
    assert _ids(signed, party="Shinde") == [deed]
    assert _ids(signed, property="44") == [deed]
    assert _ids(signed, property="44/2") == []
    assert _ids(signed, doc_date_from="2020-01-01") == [land]
    assert _ids(signed, doc_date_to="2019-03-04") == [deed]
    assert _ids(signed, has_newer_version="true") == [land]
    assert _ids(signed, sort="name", order="asc") == [deed, land]
    assert _ids(signed, sort="sensitivity", order="desc") == [deed, land]
    assert _ids(signed, sort="document_date", order="asc") == [deed, land]
    rows = {item["id"]: item for item in signed.client.get("/api/documents").json()["items"]}
    assert rows[land]["department"] == {"id": department, "name": "Land & Legal"}
    assert rows[land]["pending_suggestion"]["role"] == "candidate"
    assert rows[deed]["pending_suggestion"]["role"] == "new"
    assert rows[land]["owner"]["id"] == anita and rows[land]["versions"] == 1
    assert rows[land]["document_date"] == "2025-06-12" and rows[land]["updated_at"]
    assert signed.client.get("/api/documents", params={"sort": "size"}).status_code in (400, 422)
    assert signed.client.get("/api/documents", params={"department_id": str(uuid.uuid4())}).status_code == 404


# --- T1: Trash -------------------------------------------------------------------------------


def test_t1_a_trashed_family_is_409_on_the_file_image_readings_and_cells_until_restored(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    document = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
    api.add_page(signed.customer_id, document, 1)
    api.add_page(signed.customer_id, document, 2, unit="cells", height=1)
    api.storage.page_images[(signed.customer_id, document, 1)] = b"webp"
    routes = [
        f"/api/documents/{document}/file",
        f"/api/documents/{document}/pages/1/image",
        f"/api/documents/{document}/pages/1/readings",
        f"/api/documents/{document}/pages/2/cells",
    ]
    assert signed.client.delete(f"/api/documents/{document}", headers=CSRF).status_code == 204
    for route in routes:
        reply = signed.client.get(route)
        assert (reply.status_code, reply.json()["error"]["code"]) == (409, "in_trash"), route
    assert signed.client.post(f"/api/documents/{document}/restore", headers=CSRF).status_code == 200
    for route in routes:
        assert signed.client.get(route).status_code == 200, route
