"""File management (Phase 2 PRD 1) stage 2, versions, against PostgreSQL: the name key the same
as 0041's (V2), spotting and its suggestion (V1, V3 to V5, V8), accept, reject and Undo (V6,
V7), the note (V9), make current and mark_ready's read again (V10), compare (V11), the version
list (V12), the batch tray, the one-time sweep, section 10's version cases, the type rule on an
older version, and auto-filing's Undo back to the department. Without a database:
test_versions.py.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz import versions
from kaagaz.db import autofile, trash
from kaagaz.db import versions as versions_sql
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import _TYPE_LABEL, CONFIDENTIAL_TYPES
from tests.test_ai_settings_db import switched_off
from tests.test_api_support import CSRF, Api, Signed
from tests.test_autofile_db import organisation, upload
from tests.test_file_management import _migration
from tests.test_file_management_db import _department, _land, _place, _read
from tests.test_operator_auth_db import _owner
from tests.test_versions import _ENGLISH, _LEASE, _MARATHI, _SATBARA, _text

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
_LINK = (
    "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
    " identifier_value) VALUES (:c, :a, :b, :k, :v), (:c, :b, :a, :k, :v)"
)
_SUGGESTIONS = (
    "SELECT document_id::text, candidate_family_id::text, outcome, status, shared_identifier"
    " FROM version_suggestions WHERE customer_id = :c ORDER BY created_at"
)
_FAMILY = (
    "SELECT family_id::text, version_no, is_current, folder_id::text FROM documents"
    " WHERE customer_id = :c AND id = :d"
)


# --- helpers -----------------------------------------------------------------------------


def _spans(words: list[str], *, cells: bool = False) -> list[dict[str, Any]]:
    """The first reader's output: lines of 10 words on page 1, or one cell per word."""
    if cells:
        return [
            {
                "text": w,
                "raw": w,
                "region": {"kind": "cell", "page": 1, "row": n, "column": 1},
                "confidence": None,
            }
            for n, w in enumerate(words, 1)
        ]
    lines = [" ".join(words[i : i + 10]) for i in range(0, len(words), 10)]
    region = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.2]}
    return [{"text": line, "raw": line, "region": region, "confidence": None} for line in lines]


def _doc(
    api: Api,
    signed: Signed,
    words: list[str],
    name: str,
    *,
    hours_ago: float,
    doc_type: str | None = "land_record",
    cells: bool = False,
    **columns: Any,
) -> str:
    document = api.add_document(
        signed.customer_id,
        uploaded_by=signed.user_id,
        owner_id=signed.user_id,
        name=name,
        doc_type=doc_type,
        created_at=NOW - timedelta(hours=hours_ago),
        **columns,
    )
    api.add_reader_output(signed.customer_id, document, _spans(words, cells=cells))
    return document


def _old(db: Database, customer: str, *documents: str) -> None:
    """Documents read before version spotting: the sweep's fingerprint, never a suggestion."""
    for document in documents:
        with db.customer(customer) as conn:
            assert versions_sql.fill(conn, customer, document)


def _spot(db: Database, customer: str, document: str) -> None:
    """The worker's step after Ready: filing, then spotting (``PgDocuments.file_document``)."""
    PgDocuments(db).file_document(customer, document)


def _link(db: Database, customer: str, first: str, second: str, kind: str = "survey_number") -> None:
    with db.customer(customer) as conn:
        conn.execute(text(_LINK), {"c": customer, "a": first, "b": second, "k": kind, "v": "44"})


def _suggestions(db: Database, customer: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(_SUGGESTIONS), {"c": customer})]


def _family(db: Database, customer: str, document: str) -> tuple[Any, ...]:
    with db.customer(customer) as conn:
        return tuple(conn.execute(text(_FAMILY), {"c": customer, "d": document}).one())


def _suggestion_id(db: Database, customer: str, document: str) -> str:
    with db.customer(customer) as conn:
        return str(
            conn.execute(
                text("SELECT id FROM version_suggestions WHERE customer_id = :c AND document_id = :d"),
                {"c": customer, "d": document},
            ).scalar_one()
        )


def _satbaras(api: Api, db: Database, signed: Signed, *, folder: str | None = None) -> tuple[str, str]:
    """The worked example: the 2025 Satbara (old, in ``folder``) and the 2026 one with a
    mutation entry, uploaded elsewhere; they share survey number 44."""
    newer = [*_SATBARA[:150], *_text(21, 35, _MARATHI), *_SATBARA[150:]]
    newer[10] = "२०२६"
    old = _doc(api, signed, list(_SATBARA), "7/12 Survey 44 Wakad.pdf", hours_ago=48, folder_id=folder)
    _old(db, signed.customer_id, old)
    new = _doc(api, signed, newer, "Satbara 44.pdf", hours_ago=0)
    _link(db, signed.customer_id, old, new)
    return old, new


def _ready_run(db: Database, customer: str, document: str) -> None:
    """A run of the document that ended ready: its next ready is a read again."""
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status)"
                " VALUES (:c, :d, :t, :t, 'ready')"
            ),
            {"c": customer, "d": document, "t": NOW - timedelta(days=1)},
        )


# --- V2 --------------------------------------------------------------------------------


def test_v2_the_python_name_key_is_0041s_sql_name_key(db: Database) -> None:
    names = [
        "7/12 Survey 44 Wakad v2 (1).pdf",
        "Rent Agreement Shinde FINAL copy.docx",
        "सातबारा ४४ - 12.06.2026.pdf",
        "गट नं. १२३_४ ver 2.PDF",
        "Invoice_ver 3 – latest.PDF",  # noqa: RUF001 - an en dash, on purpose
        "Rev. plan “A”.xlsx",
        "Version2 draft",
        "review v 2x",
        "report.tar.gz",
        "  Stock   register (12) Oct-2026.xlsx",
    ]
    rule = _migration().NAME_KEY
    statement = text(f"SELECT {rule} FROM (SELECT CAST(:n AS text) AS display_name) AS t")  # noqa: S608
    customer, _ = organisation(db)
    with db.customer(customer) as conn:
        for name in names:
            assert conn.execute(statement, {"n": name}).scalar_one() == versions.name_key(name), name


# --- V1, V3 to V6, V9, V12: the worked example ---------------------------------------------


def test_v1_to_v6_next_years_satbara_is_suggested_then_accepted_into_the_familys_folder(db: Database) -> None:
    """Section 10 too: accepting a suggestion for a document in another folder moves it, and
    the activity says so."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    land = api.add_folder(customer, signed.user_id, "Survey 44, Wakad")
    old, new = _satbaras(api, db, signed, folder=land)
    tag = api.add_tag(customer, signed.user_id, "Land")
    api.tag_document(customer, old, tag)
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET sensitivity = 'restricted' WHERE id = :d"), {"d": old})
    _spot(db, customer, new)

    with db.customer(customer) as conn:
        minhash, bands, key = conn.execute(
            text("SELECT text_minhash, text_bands, name_key FROM documents WHERE id = :d"), {"d": new}
        ).one()
    assert (len(minhash), len(bands), key) == (64, 16, "satbara 44")
    assert _suggestions(db, customer) == [(new, old, "suggested", "pending", "survey_number")]
    assert (
        "version.suggested",
        "document",
        new,
        {"suggestion_id": _suggestion_id(db, customer, new), "candidate_family": old, "outcome": "suggested"},
    ) in api.audit_rows(customer)

    detail = signed.client.get(f"/api/documents/{new}").json()
    banner = detail["version_suggestion"]
    assert (banner["role"], banner["candidate"]["name"], banner["next_version_no"]) == (
        "new",
        "7/12 Survey 44 Wakad.pdf",
        2,
    )
    assert banner["reasons"].endswith("% of the text is the same and the survey number matches")
    assert signed.client.get(f"/api/documents/{old}").json()["version_suggestion"]["role"] == "candidate"
    listed = signed.client.get("/api/documents", params={"has_newer_version": "true"}).json()["items"]
    assert [item["id"] for item in listed] == [old]

    reply = signed.client.post(
        f"/api/version-suggestions/{banner['id']}/accept",
        json={"version_note": "Mutation 1234 added"},
        headers=CSRF,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["version_no"], body["family_id"], body["folder_id"]) == (2, old, land)
    assert (body["sensitivity"], body["version_note"], [t["id"] for t in body["tags"]]) == (
        "restricted",
        "Mutation 1234 added",
        [tag],
    )
    assert _family(db, customer, new) == (old, 2, True, land)
    assert _family(db, customer, old)[2] is False
    assert _suggestions(db, customer)[0][3] == "accepted"
    accepted = [row for row in api.audit_rows(customer) if row[0] == "version.accepted"]
    assert accepted[0][3] | {"suggestion_id": None} == {
        "suggestion_id": None,
        "family_id": old,
        "version_no": 2,
        "outcome": "suggested",
        "from_folder_id": None,
        "folder_id": land,
    }
    shown = signed.client.get(f"/api/documents/{old}").json()
    assert [
        (v["version_no"], v["joined"], v["is_current"], v["version_note"]) for v in shown["versions"]
    ] == [
        (1, "uploaded", False, None),
        (2, "linked", True, "Mutation 1234 added"),
    ]
    assert shown["versions"][1]["uploaded_by"]["id"] == signed.user_id and shown["version_suggestion"] is None
    again = signed.client.post(f"/api/version-suggestions/{banner['id']}/accept", headers=CSRF)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "not_found")  # no longer on its own


def test_v4_a_satbara_and_its_sale_deed_stay_related_never_versions(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    satbara = _doc(api, signed, list(_SATBARA), "7/12 Survey 44 Wakad.pdf", hours_ago=48)
    _old(db, signed.customer_id, satbara)
    deed = _doc(
        api,
        signed,
        _text(30, 400, _MARATHI + _ENGLISH),
        "Sale Deed Shinde Survey 44.pdf",
        hours_ago=0,
        doc_type="contract",
    )
    _link(db, signed.customer_id, satbara, deed)
    _spot(db, signed.customer_id, deed)
    assert _suggestions(db, signed.customer_id) == []


# --- V4 auto, V7 Undo ---------------------------------------------------------------------


def _leases(api: Api, db: Database, signed: Signed) -> tuple[str, str, str]:
    """A lease in a folder, and the same lease with one word changed, same name, filed there."""
    folder = api.add_folder(signed.customer_id, signed.user_id, "Leases")
    changed = [*_LEASE[:150], "twelve", *_LEASE[151:]]
    old = _doc(
        api,
        signed,
        list(_LEASE),
        "Rent Agreement Shinde.pdf",
        hours_ago=48,
        doc_type="contract",
        folder_id=folder,
    )
    _old(db, signed.customer_id, old)
    batch = str(uuid.uuid4())
    new = _doc(
        api,
        signed,
        changed,
        "Rent Agreement Shinde v2.pdf",
        hours_ago=0,
        doc_type="contract",
        folder_id=folder,
        batch_id=batch,
    )
    return old, new, batch


def test_v4_an_almost_exact_copy_is_linked_on_its_own_and_v7_undo_puts_it_back(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new, batch = _leases(api, db, signed)
    _spot(db, customer, new)
    folder = _family(db, customer, old)[3]
    assert _suggestions(db, customer) == [(new, old, "auto", "accepted", None)]
    assert _family(db, customer, new) == (old, 2, True, folder)
    tray = signed.client.get(f"/api/batches/{batch}").json()
    assert tray["version_suggestion_count"] == 1
    assert (tray["version_suggestions"][0]["outcome"], tray["version_suggestions"][0]["candidate"]["id"]) == (
        "auto",
        old,
    )
    versions_shown = signed.client.get(f"/api/documents/{new}").json()["versions"]
    assert [v["joined"] for v in versions_shown] == ["uploaded", "auto"]

    with db.customer(customer) as conn:  # a person moved the family since: Undo goes back
        conn.execute(text("UPDATE documents SET folder_id = NULL WHERE family_id = :f"), {"f": old})
    undone = signed.client.post(f"/api/documents/{new}/version-undo", headers=CSRF)
    assert undone.status_code == 200, undone.text
    assert (undone.json()["family_id"], undone.json()["version_no"]) == (new, 1)
    assert _family(db, customer, new) == (new, 1, True, folder)  # the folder it had before the link
    assert _family(db, customer, old)[1:3] == (1, True)  # current again
    assert _suggestions(db, customer)[0][3] == "undone"
    assert [row[0] for row in api.audit_rows(customer) if row[0].startswith("version.")] == [
        "version.suggested",
        "version.accepted",
        "version.undone",
    ]
    # A5: the family's timeline keeps the linked version's whole story, undo included.
    timeline = signed.client.get(f"/api/documents/{old}/activity").json()["items"]
    shown = {(item["action"], item["target_id"]) for item in timeline}
    for action in ("version.suggested", "version.accepted", "version.undone"):
        assert (action, new) in shown, action
    twice = signed.client.post(f"/api/documents/{new}/version-undo", headers=CSRF)
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "not_linked")


def test_section_10_undo_after_a_third_version_is_not_newest(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    old, new, _ = _leases(api, db, signed)
    _spot(db, signed.customer_id, new)
    api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, family_id=old, version_no=3, is_current=False
    )
    reply = signed.client.post(f"/api/documents/{new}/version-undo", headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "not_newest")
    assert _family(db, signed.customer_id, new)[:2] == (old, 2)


# --- V6 reject, V8 the switch ----------------------------------------------------------------


def test_v6_keep_separate_is_never_suggested_again(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    _, new = _satbaras(api, db, signed)
    _spot(db, customer, new)
    suggestion = _suggestion_id(db, customer, new)
    reply = signed.client.post(f"/api/version-suggestions/{suggestion}/reject", headers=CSRF)
    assert (reply.status_code, reply.json()) == (
        200,
        {"id": suggestion, "status": "rejected", "document_id": new},
    )
    assert "version.rejected" in api.audit_actions(customer)
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "none"  # the pair is never suggested again
    assert len(_suggestions(db, customer)) == 1
    for action in ("accept", "reject"):
        late = signed.client.post(f"/api/version-suggestions/{suggestion}/{action}", headers=CSRF)
        assert (late.status_code, late.json()["error"]["code"]) == (409, "already_decided")
    assert signed.client.get(f"/api/documents/{new}").json()["version_suggestion"] is None


def test_v8_spotting_is_off_while_the_organisations_or_the_platforms_switch_is(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    _, new = _satbaras(api, db, signed)
    _owner(
        db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled)"
        " VALUES (:c, 'version_spotting', false)",
        {"c": customer},
    )
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "off"
    _owner(db, "DELETE FROM org_feature_switches WHERE customer_id = :c", {"c": customer})
    with switched_off(db, "version_spotting"), db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "off"
    assert _suggestions(db, customer) == []
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "suggested"  # both on


# --- section 10 -------------------------------------------------------------------------


def test_section_10_a_suggestion_whose_candidate_went_to_trash_disappears(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new = _satbaras(api, db, signed)
    _spot(db, customer, new)
    suggestion = _suggestion_id(db, customer, new)
    with db.customer(customer) as conn:
        assert trash.trash_document(conn, customer, old, by=signed.user_id, now=NOW)
    assert signed.client.get(f"/api/documents/{new}").json()["version_suggestion"] is None
    rows = {item["id"]: item for item in signed.client.get("/api/documents").json()["items"]}
    assert rows[new]["pending_suggestion"] is None
    assert signed.client.get("/api/dashboard").json()["tiles"]["versions_to_confirm"] == 0
    reply = signed.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF)
    assert reply.status_code == 404
    assert _family(db, customer, new)[:2] == (new, 1)  # stays separate


def test_section_10_two_of_one_batch_the_later_is_suggested_once_both_are_ready(db: Database) -> None:
    """The later upload read first finds nothing; the earlier one, once ready, suggests it."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    batch = str(uuid.uuid4())
    first = _doc(api, signed, list(_SATBARA), "Satbara 2025.pdf", hours_ago=1, batch_id=batch, status="clean")
    later = [*_SATBARA[:200], "फेरफार", *_SATBARA[201:]]
    second = _doc(api, signed, later, "Satbara 2026.pdf", hours_ago=0.5, batch_id=batch)
    _spot(db, customer, second)
    assert _suggestions(db, customer) == []
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE documents SET status = 'ready' WHERE id = :d"), {"d": first})
    _spot(db, customer, first)
    assert [row[:2] for row in _suggestions(db, customer)] == [(second, first)]
    assert signed.client.get(f"/api/batches/{batch}").json()["version_suggestion_count"] == 1


def test_section_10_two_equal_families_the_newest_current_one_wins(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    older = _doc(api, signed, list(_SATBARA), "Copy A.pdf", hours_ago=72)
    newer = _doc(api, signed, list(_SATBARA), "Copy B.pdf", hours_ago=24)
    _old(db, customer, older, newer)  # read before spotting: never suggested to each other
    upload_ = _doc(api, signed, [*_SATBARA[:100], "नवीन", *_SATBARA[101:]], "Satbara new.pdf", hours_ago=0)
    _spot(db, customer, upload_)
    assert [row[:2] for row in _suggestions(db, customer)] == [(upload_, newer)]


def test_v10_make_current_then_a_read_again_of_the_newest_keeps_the_older_current(db: Database) -> None:
    """Verify (V10): a read again never changes which version is current; a new upload still
    becomes current when it is ready."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    first = api.add_document(customer, uploaded_by=signed.user_id, is_current=False)
    second = api.add_document(customer, uploaded_by=signed.user_id, family_id=first, version_no=2)
    reply = signed.client.post(f"/api/documents/{first}/make-current", headers=CSRF)
    assert (reply.status_code, reply.json()["id"]) == (200, first)
    assert [_family(db, customer, d)[2] for d in (first, second)] == [True, False]
    assert "version.make_current" in api.audit_actions(customer)
    _ready_run(db, customer, second)  # it was read before
    PgDocuments(db).mark_ready(customer, second, 1)  # the read again ends
    assert [_family(db, customer, d)[2] for d in (first, second)] == [True, False]
    third = api.add_document(
        customer, uploaded_by=signed.user_id, family_id=first, version_no=3, is_current=False, status="clean"
    )
    PgDocuments(db).mark_ready(customer, third, 1)  # a new upload's first ready
    assert [_family(db, customer, d)[2] for d in (first, second, third)] == [False, False, True]
    _ready_run(db, customer, first)
    PgDocuments(db).mark_ready(customer, first, 1)  # an older version read again: still not current
    assert _family(db, customer, third)[2] is True
    reading = api.add_document(
        customer,
        uploaded_by=signed.user_id,
        family_id=first,
        version_no=4,
        is_current=False,
        status="received",
    )
    refused = signed.client.post(f"/api/documents/{reading}/make-current", headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "not_ready")
    with db.customer(customer) as conn:
        assert trash.trash_document(conn, customer, first, by=signed.user_id, now=NOW)
    trashed = signed.client.post(f"/api/documents/{second}/make-current", headers=CSRF)
    assert (trashed.status_code, trashed.json()["error"]["code"]) == (409, "in_trash")


def test_section_10_a_spreadsheet_with_one_changed_cell_is_suggested(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    cells = [f"{name}-{n}" for n, name in enumerate(_text(5, 200))]
    old = _doc(
        api,
        signed,
        cells,
        "Stock register.xlsx",
        hours_ago=24,
        doc_type=None,
        cells=True,
        detected_type="zip",
    )
    _old(db, customer, old)
    changed = [*cells[:80], "cement-999", *cells[81:]]
    new = _doc(
        api,
        signed,
        changed,
        "Stock register.xlsx",
        hours_ago=0,
        doc_type=None,
        cells=True,
        detected_type="zip",
    )
    _spot(db, customer, new)
    assert [row[:3] for row in _suggestions(db, customer)] == [(new, old, "suggested")]


# --- V11 compare ---------------------------------------------------------------------------


def test_v11_compare_a_pending_pair_or_one_family_and_nothing_else(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new = _satbaras(api, db, signed)
    stranger = _doc(api, signed, _text(9, 50), "Other.pdf", hours_ago=10)
    assert signed.client.get(f"/api/documents/{new}/compare", params={"with": old}).status_code == 404
    _spot(db, customer, new)
    reply = signed.client.get(f"/api/documents/{new}/compare", params={"with": old})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["left_id"], body["right_id"], body["summary"]) == (old, new, "2 changes on page 1")
    assert {run["kind"] for run in body["pages"][0]["runs"]} == {"same", "added", "removed"}
    assert signed.client.get(f"/api/documents/{new}/compare", params={"with": stranger}).status_code == 404
    assert (
        signed.client.get(f"/api/documents/{new}/compare", params={"with": str(uuid.uuid4())}).status_code
        == 404
    )
    suggestion = _suggestion_id(db, customer, new)
    assert (
        signed.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF).status_code == 200
    )
    assert (
        signed.client.get(f"/api/documents/{old}/compare", params={"with": new}).status_code == 200
    )  # a family
    with db.customer(customer) as conn:
        assert trash.trash_document(conn, customer, old, by=signed.user_id, now=NOW)
    trashed = signed.client.get(f"/api/documents/{old}/compare", params={"with": new})
    assert (trashed.status_code, trashed.json()["error"]["code"]) == (409, "in_trash")


# --- V9 the note at upload -----------------------------------------------------------------


def test_v9_upload_new_version_takes_an_optional_note(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
    files = {"file": ("v2.pdf", b"%PDF-1.7\n" + b"9" * 200, "application/pdf")}
    long = signed.client.post(
        f"/api/documents/{first}/versions", data={"version_note": "x" * 201}, files=files, headers=CSRF
    )
    assert long.status_code == 400
    reply = signed.client.post(
        f"/api/documents/{first}/versions",
        data={"version_note": "  Mutation   1234 added "},
        files=files,
        headers=CSRF,
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["document"]["version_note"] == "Mutation 1234 added"


# --- the one-time sweep, the type rule, auto-filing's Undo -----------------------------------------


def test_the_sweep_fingerprints_old_documents_and_never_suggests(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    first = _doc(api, signed, list(_SATBARA), "A.pdf", hours_ago=2)
    second = _doc(api, signed, list(_SATBARA), "A.pdf", hours_ago=1)
    short = _doc(api, signed, ["too", "short"], "B.pdf", hours_ago=1)
    with db.customer(customer) as conn:
        found = versions_sql.missing(conn, customer, 10)
    assert set(found) == {first, second, short}
    _old(db, customer, *found)
    with db.customer(customer) as conn:
        assert versions_sql.missing(conn, customer, 10) == []
        empty: object = conn.execute(
            text("SELECT text_minhash FROM documents WHERE id = :d"), {"d": short}
        ).scalar_one()
    assert empty == [] and _suggestions(db, customer) == []


def test_the_type_rule_on_an_older_version_never_relabels_the_family(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    first = api.add_document(customer, uploaded_by=signed.user_id, is_current=False)
    api.add_document(customer, uploaded_by=signed.user_id, family_id=first, version_no=2)
    with db.customer(customer) as conn:
        params = {"c": customer, "d": first, "doc_type": "land_record", "secret": list(CONFIDENTIAL_TYPES)}
        conn.execute(_TYPE_LABEL, params)  # the older version read again as a land record
        labels = conn.execute(
            text("SELECT DISTINCT sensitivity, is_current FROM documents WHERE family_id = :f"), {"f": first}
        ).all()
    assert sorted(tuple(row) for row in labels) == [("internal", False), ("internal", True)]


def test_autofile_undo_after_filing_into_a_department_goes_back_to_the_department(db: Database) -> None:
    """Stage 1's bug: Undo moved a file filed inside a department to the top level."""
    customer, admin = organisation(db)
    department = _department(db, customer, admin)
    satbara = upload(db, customer, admin, folder=department)
    assert _read(db, customer, satbara, "land_record", _land("44")) == "filed"
    assert _place(db, customer, satbara)[0] == "Survey 44, Wakad"
    with db.customer(customer) as conn:
        family = trash.lock_live_family(conn, customer, satbara)
        assert family is not None and autofile.undo(conn, customer, family) is not None
        folder = conn.execute(text("SELECT folder_id::text FROM documents WHERE id = :d"), {"d": satbara})
        assert folder.scalar_one() == department
    # Changed on purpose (#239, 27 Oct 2026): an upload with no department is routed to the one
    # its type maps to (24cea75, land records to Land & Legal), so Undo goes back to that one.
    loose = upload(db, customer, admin)
    _read(db, customer, loose, "land_record", _land("51"))
    with db.customer(customer) as conn:
        family = trash.lock_live_family(conn, customer, loose)
        assert family is not None and autofile.undo(conn, customer, family) is not None
        folder = conn.execute(text("SELECT folder_id::text FROM documents WHERE id = :d"), {"d": loose})
        mapped = conn.execute(
            text("SELECT id::text FROM folders WHERE name = 'Land & Legal' AND kind = 'department'")
        )
        assert folder.scalar_one() == mapped.scalar_one()
