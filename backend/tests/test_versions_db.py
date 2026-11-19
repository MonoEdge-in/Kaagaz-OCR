"""File management (Phase 2 PRD 1) stage 2, versions, against PostgreSQL: the name key the same
as 0041's (V2), spotting and its suggestion (V1, V3 to V5, V8), accept, reject and Undo (V6,
V7), the note (V9), make current and mark_ready's read again (V10), compare (V11), the version
list (V12), the batch tray, the one-time sweep, section 10's version cases, the type rule on an
older version, auto-filing's Undo back to the department, and #521's 80% line counted exactly,
never linked on its own. Without a database:
test_versions.py.
"""

import contextlib
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz import versions
from kaagaz.db import ai_settings, autofile, trash
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
from tests.test_versions import (
    _ENGLISH,
    _ITEMS,
    _MARATHI,
    _SATBARA,
    PLOT,
    PLOT_EDITED,
    _extended,
    _invoice,
    _name_similarity,
    _text,
)

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
    # Changed on purpose (#521, NB-17): the exact share of the text, the banner's brackets.
    assert (banner["reasons"], banner["text_overlap_estimated"]) == ("84% the same text", False)
    assert round(banner["text_overlap"], 4) == 0.8441
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


# --- V4 never on its own (#521), V7 Undo -----------------------------------------------------
_LONG_LEASE = _text(10, 1200)


def _leases(api: Api, db: Database, signed: Signed) -> tuple[str, str, str]:
    """A lease in a folder, and the same lease with one word changed (99% of the text), same
    name and type, filed there: the old automatic link's case."""
    folder = api.add_folder(signed.customer_id, signed.user_id, "Leases")
    changed = [*_LONG_LEASE[:600], "twelve", *_LONG_LEASE[601:]]
    old = _doc(
        api,
        signed,
        list(_LONG_LEASE),
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


def _accept(signed: Signed, db: Database, document: str) -> None:
    suggestion = _suggestion_id(db, signed.customer_id, document)
    reply = signed.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF)
    assert reply.status_code == 200, reply.text


def test_521_the_old_automatic_case_is_only_suggested_and_v7_undo_after_accepting_puts_it_back(
    db: Database,
) -> None:
    """Changed on purpose (#521, Tushar, 6 Nov 2026): same name, same folder and type, 99% of the
    text, was linked on its own; now a person decides, and Undo still works after Accept."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new, batch = _leases(api, db, signed)
    _spot(db, customer, new)
    folder = _family(db, customer, old)[3]
    assert _suggestions(db, customer) == [(new, old, "suggested", "pending", None)]
    assert _family(db, customer, new) == (new, 1, True, folder)  # still its own document
    assert "version.accepted" not in api.audit_actions(customer)
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert (banner["outcome"], banner["status"]) == ("suggested", "pending")
    assert banner["reasons"] == "99% the same text"
    tray = signed.client.get(f"/api/batches/{batch}").json()
    assert tray["version_suggestion_count"] == 1
    assert tray["version_suggestions"][0]["outcome"] == "suggested"

    _accept(signed, db, new)
    assert _family(db, customer, new) == (old, 2, True, folder)
    versions_shown = signed.client.get(f"/api/documents/{new}").json()["versions"]
    assert [v["joined"] for v in versions_shown] == ["uploaded", "linked"]
    with db.customer(customer) as conn:  # a person moved the family since: Undo goes back
        conn.execute(text("UPDATE documents SET folder_id = NULL WHERE family_id = :f"), {"f": old})
    undone = signed.client.post(f"/api/documents/{new}/version-undo", headers=CSRF)
    assert undone.status_code == 200, undone.text
    assert _family(db, customer, new) == (new, 1, True, folder)  # the folder it had before the link
    assert _family(db, customer, old)[1:3] == (1, True)  # current again
    assert _suggestions(db, customer)[0][3] == "undone"


def test_521_an_automatic_link_made_before_stays_and_v7_undo_puts_it_back(db: Database) -> None:
    """A suggestion linked on its own before #521 (outcome auto, accepted by no one) is left as it
    is: the tray and the version list still say so, and Undo still works."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new, batch = _leases(api, db, signed)
    _spot(db, customer, new)
    with db.customer(customer) as conn:  # as the code before #521 left it
        conn.execute(text("UPDATE version_suggestions SET outcome = 'auto', text_overlap_exact = NULL"))
        versions_sql.link(conn, customer, _suggestion_id(db, customer, new), by=None)
    folder = _family(db, customer, old)[3]
    assert _suggestions(db, customer) == [(new, old, "auto", "accepted", None)]
    assert _family(db, customer, new) == (old, 2, True, folder)
    tray = signed.client.get(f"/api/batches/{batch}").json()
    assert tray["version_suggestion_count"] == 1
    assert (tray["version_suggestions"][0]["outcome"], tray["version_suggestions"][0]["candidate"]["id"]) == (
        "auto",
        old,
    )
    assert tray["version_suggestions"][0]["text_overlap_estimated"] is True  # from before 0076
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
    _accept(signed, db, new)  # Changed on purpose (#521): never linked on its own now
    api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, family_id=old, version_no=3, is_current=False
    )
    reply = signed.client.post(f"/api/documents/{new}/version-undo", headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "not_newest")
    assert _family(db, signed.customer_id, new)[:2] == (old, 2)


# --- #521: the 80% line, counted exactly ------------------------------------------------------
_STORED = "SELECT text_overlap, text_overlap_exact FROM version_suggestions WHERE customer_id = :c"


def _stored(db: Database, customer: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [(round(float(t), 4), exact) for t, exact in conn.execute(text(_STORED), {"c": customer})]


def _pair(api: Api, db: Database, signed: Signed, texts: tuple[str, str], names: tuple[str, str]) -> str:
    """An old document and a new upload, of one type, with no link: the new one's id."""
    old = _doc(api, signed, texts[0].split(), names[0], hours_ago=48, doc_type="contract")
    _old(db, signed.customer_id, old)
    return _doc(api, signed, texts[1].split(), names[1], hours_ago=0, doc_type="contract")


def test_521_exactly_80_percent_of_the_text_alone_is_suggested(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    new = _pair(api, db, signed, _extended(80, 20), ("Lease deed Shinde.pdf", "Scan 0001.pdf"))
    _spot(db, signed.customer_id, new)
    assert [row[2:4] for row in _suggestions(db, signed.customer_id)] == [("suggested", "pending")]
    assert _stored(db, signed.customer_id) == [(0.8, True)]
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert (banner["reasons"], banner["text_overlap_estimated"]) == ("80% the same text", False)


def test_521_79_percent_of_the_text_is_not_suggested_even_with_the_same_name(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    new = _pair(api, db, signed, _extended(79, 21), ("Lease deed Shinde.pdf", "Lease deed Shinde.pdf"))
    with db.customer(signed.customer_id) as conn:
        assert versions_sql.spot(conn, signed.customer_id, new) == "none"  # found by its name, not enough
    assert _suggestions(db, signed.customer_id) == []


def test_521_an_estimate_of_83_with_77_counted_exactly_is_not_suggested(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    signed = api.new_signed()
    new = _pair(api, db, signed, _extended(77, 23), ("Lease deed Shinde.pdf", "Lease deed Shinde.pdf"))
    monkeypatch.setattr(versions, "overlap", lambda *_: 0.83)
    with db.customer(signed.customer_id) as conn:
        assert versions_sql.spot(conn, signed.customer_id, new) == "none"
    assert _suggestions(db, signed.customer_id) == []


def test_521_an_estimate_of_77_with_83_counted_exactly_is_suggested(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    signed = api.new_signed()
    new = _pair(api, db, signed, _extended(83, 17), ("Lease deed Shinde.pdf", "Lease deed Shinde.pdf"))
    monkeypatch.setattr(versions, "overlap", lambda *_: 0.77)
    with db.customer(signed.customer_id) as conn:
        assert versions_sql.spot(conn, signed.customer_id, new) == "suggested"
    assert _stored(db, signed.customer_id) == [(0.83, True)]
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert banner["reasons"] == "83% the same text"


def test_521_over_the_compare_limits_the_estimate_decides_and_is_shown_as_one(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions, "MAX_COMPARE_TOTAL", 250)  # the Satbaras have 300 and 335 words
    api = Api(db)
    signed = api.new_signed()
    _, new = _satbaras(api, db, signed)
    _spot(db, signed.customer_id, new)
    assert _stored(db, signed.customer_id) == [(0.8281, False)]  # 53 of the 64 values (84% counted)
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert (banner["reasons"], banner["text_overlap_estimated"]) == ("about 82% the same text", True)


def test_521_a_copy_in_another_organisation_is_never_suggested(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    theirs = _doc(api, a, list(_LONG_LEASE), "Rent Agreement Shinde.pdf", hours_ago=48, doc_type="contract")
    _old(db, a.customer_id, theirs)
    copy = [*_LONG_LEASE[:600], "twelve", *_LONG_LEASE[601:]]
    mine = _doc(api, b, copy, "Rent Agreement Shinde.pdf", hours_ago=0, doc_type="contract")
    with db.customer(b.customer_id) as conn:
        assert versions_sql.spot(conn, b.customer_id, mine) == "none"
    assert _suggestions(db, a.customer_id) == _suggestions(db, b.customer_id) == []


# --- #521, the council's three bug fixes -------------------------------------------------------


def test_521_a_99_percent_match_under_another_name_wins_over_20_same_named_ones(db: Database) -> None:
    """Fix 1: the candidates were the first 20 by name, then newest, so a 99% match under another
    name was dropped behind 20 same-named ones; now up to 200 are ranked by the estimate first."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    base = _text(11, 500)
    for n in range(20):  # 90% the same text, the same name
        words = list(base)
        for place in (5 + 7 * n, 150 + 7 * n, 300 + 7 * n):
            words[place] = f"z{n}x{place}"
        _old(db, customer, _doc(api, signed, words, "Lease.pdf", hours_ago=48 + n, doc_type="contract"))
    scan = _doc(api, signed, [*base[:200], "twelve", *base[201:]], "Scan 7.pdf", hours_ago=100)
    _old(db, customer, scan)
    new = _doc(api, signed, list(base), "Lease.pdf", hours_ago=0, doc_type="contract")
    _spot(db, customer, new)
    assert [row[:2] for row in _suggestions(db, customer)] == [(new, scan)]


def test_521_monthly_bills_with_one_name_and_account_number_are_not_suggested(db: Database) -> None:
    """Fix 2: "Invoice 2025-01" and "Invoice 2025-02", one template, other numbers (44% of the
    text), the same account number: name and identifier are two signals, but under half the text."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    later = [(what, hsn, qty - 3, rate + 5) for what, hsn, qty, rate in _ITEMS]
    names = ("Invoice 2025-01.pdf", "Invoice 2025-02.pdf")
    assert _name_similarity(*names) >= versions.NAME_SIGNAL
    january = _invoice("SHT/2025/0101", "31-01-2025", _ITEMS).split()
    old = _doc(api, signed, january, names[0], hours_ago=48, doc_type="invoice")
    _old(db, customer, old)
    february = _invoice("SHT/2025/0202", "28-02-2025", later).split()
    new = _doc(api, signed, february, names[1], hours_ago=0, doc_type="invoice")
    _link(db, customer, old, new, "account_number")
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "none"
    assert _suggestions(db, customer) == []


def test_prd9_f06_a_short_file_uploaded_again_with_two_values_edited_is_suggested(db: Database) -> None:
    """PRD 9 F-06 (VER-01): plot data.json, 11 words, uploaded again under its name with two values
    edited. Under MIN_WORDS it has no fingerprint, so only the name counted (one signal) and nothing
    was suggested; now the two short texts are compared word by word (9 of 11). Accepted (the
    pop-up's Yes, the banner's Accept), it is version 2 and current."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old = _doc(api, signed, PLOT.split(), "plot data.json", hours_ago=48, doc_type=None)
    _old(db, customer, old)
    new = _doc(api, signed, PLOT_EDITED.split(), "plot data.json", hours_ago=0, doc_type=None)
    _spot(db, customer, new)
    assert [row[:4] for row in _suggestions(db, customer)] == [(new, old, "suggested", "pending")]
    assert _stored(db, customer) == [(0.8182, True)]
    assert _family(db, customer, new)[:3] == (new, 1, True)  # never linked on its own (#521)
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert banner["reasons"] == "81% the same text"
    suggestion = _suggestion_id(db, customer, new)
    accepted = signed.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF)
    assert accepted.status_code == 200, accepted.text
    assert _family(db, customer, new)[:3] == (old, 2, True)
    assert _family(db, customer, old)[:3] == (old, 1, False)


def test_prd9_f06_a_short_file_with_other_words_under_its_name_is_not_suggested(db: Database) -> None:
    """The same name and a short text, but under 80% of the words in common: one signal only."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old = _doc(api, signed, PLOT.split(), "plot data.json", hours_ago=48, doc_type=None)
    _old(db, customer, old)
    other = '{"layout": "Wakad", "survey": [9, 10], "plots_total": 3, "contact": "Head office"}'
    new = _doc(api, signed, other.split(), "plot data.json", hours_ago=0, doc_type=None)
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "none"
    assert _suggestions(db, customer) == []


def test_prd9_f06_short_monthly_bills_of_one_name_and_account_number_are_not_suggested(db: Database) -> None:
    """Review of #678: #521's monthly bills, but short (60 words): one name key and account number,
    a quarter of the words other numbers. Word by word 75%, under the 80% the short rule needs, so
    the share never stands for the floor of name and identifier."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    template = _text(12, 45)

    def bill(month: int) -> list[str]:
        words = list(template)
        for place in range(15):
            words.insert(place * 4, f"{month}{place:02}")
        return words

    names = ("Invoice 2025-01.pdf", "Invoice 2025-02.pdf")
    old = _doc(api, signed, bill(1), names[0], hours_ago=48, doc_type="invoice")
    _old(db, customer, old)
    new = _doc(api, signed, bill(2), names[1], hours_ago=0, doc_type="invoice")
    _link(db, customer, old, new, "account_number")
    assert versions.word_share({1: " ".join(bill(1))}, {1: " ".join(bill(2))}) == 0.75
    with db.customer(customer) as conn:
        assert versions_sql.spot(conn, customer, new) == "none"
    assert _suggestions(db, customer) == []


def test_521_two_near_copies_of_one_batch_spotted_at_once_are_matched_once(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 3: the fingerprint is written with Ready, so two spot runs at once see each other's.
    Changed on purpose (review, 6 Nov 2026): no lock per customer. Both runs meet at the switch
    check and go on together; each suggests the same pair (the later file as the new version),
    and the unique pair keeps one, so the second says none."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    batch = str(uuid.uuid4())
    first = _doc(api, signed, list(_SATBARA), "Satbara A.pdf", hours_ago=1, batch_id=batch, status="clean")
    near = [*_SATBARA[:200], "फेरफार", *_SATBARA[201:]]
    second = _doc(api, signed, near, "Satbara B.pdf", hours_ago=0.5, batch_id=batch, status="clean")
    for document in (first, second):
        PgDocuments(db).mark_ready(customer, document, 1)
    meet = threading.Barrier(2)
    switch_on = ai_settings.switch_on

    def waiting(*args: Any) -> bool:
        with contextlib.suppress(threading.BrokenBarrierError):  # the lock kept the other out
            meet.wait(timeout=3)
        return switch_on(*args)

    monkeypatch.setattr(ai_settings, "switch_on", waiting)
    codes: list[str | None] = []

    def run(document: str) -> None:
        with db.customer(customer) as conn:
            codes.append(versions_sql.spot(conn, customer, document))

    threads = [threading.Thread(target=run, args=(document,)) for document in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(code or "" for code in codes) == ["none", "suggested"]
    pairs = [set(row[:2]) for row in _suggestions(db, customer)]
    assert pairs == [{first, second}]


def test_521_a_fingerprint_that_fails_never_fails_ready(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review fix 6: the fingerprint is written in a savepoint of Ready's transaction; an error
    there is logged with the ids only, and the document is still ready, with no fingerprint."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    document = _doc(api, signed, list(_SATBARA), "Satbara secret name.pdf", hours_ago=0, status="clean")

    def broken(conn: Any, *_args: Any) -> None:
        conn.execute(text("SELECT 1 / 0"))  # a real SQL error, so the transaction is hit too

    monkeypatch.setattr(versions_sql, "write_fingerprint", broken)
    with caplog.at_level("WARNING"):
        PgDocuments(db).mark_ready(customer, document, 1)
    with db.customer(customer) as conn:
        status, minhash = conn.execute(
            text("SELECT status, text_minhash FROM documents WHERE id = :d"), {"d": document}
        ).one()
    assert (status, minhash) == ("ready", None)
    logged = [record for record in caplog.records if record.getMessage() == "fingerprint_failed"]
    assert len(logged) == 1 and "secret" not in str(logged[0].__dict__)


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


def test_521_see_the_differences_of_a_suggested_pair_says_it_in_plain_words(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suggested pair's compare: the share and the changes in words; another organisation's
    pair is 404; over the compare limits it says only the first part is compared."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new = _satbaras(api, db, signed)
    _spot(db, customer, new)
    reply = signed.client.get(f"/api/documents/{new}/compare", params={"with": old})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["overview"] == "84% the same. 36 words changed on page 1."
    assert (body["pages"][0]["left_words"], body["pages"][0]["right_words"]) == (300, 335)
    assert (body["left_pages"], body["right_pages"]) == (1, 1)  # review fix 9: a page each side has
    other = api.new_signed()
    assert other.client.get(f"/api/documents/{new}/compare", params={"with": old}).status_code == 404
    monkeypatch.setattr(versions, "MAX_COMPARE_TOTAL", 100)
    long = signed.client.get(f"/api/documents/{new}/compare", params={"with": old}).json()
    assert (long["truncated"], long["overview"]) == (
        True,
        "These files are long, so only the first part is compared.",
    )


def test_prd9_f08_compare_says_which_pages_of_each_side_have_a_picture(db: Database) -> None:
    """PRD 9 F-08 (VER-07): the reply names each side's pages with a picture (``pages.has_image``),
    so the screen asks for page images of those only: none for a text file (a sheet of cells), the
    scan's pages for a PDF."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old, new = _satbaras(api, db, signed)
    api.add_page(customer, old, 1, unit="cells")
    for page_no in (1, 2):
        api.add_page(customer, new, page_no)
    _spot(db, customer, new)
    body = signed.client.get(f"/api/documents/{new}/compare", params={"with": old}).json()
    assert body["image_pages"] == {"left": [], "right": [1, 2]}


def test_521_the_same_text_in_other_bytes_is_a_near_match_whose_text_is_the_same(db: Database) -> None:
    """A re-scan or a re-save: other bytes (not the duplicate), the text counted exactly the same,
    so the banner may say 100% and the compare says so in words."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    old = _doc(api, signed, list(_SATBARA), "Satbara.pdf", hours_ago=48)
    _old(db, customer, old)
    new = _doc(api, signed, list(_SATBARA), "Satbara scan.pdf", hours_ago=0)
    _spot(db, customer, new)
    banner = signed.client.get(f"/api/documents/{new}").json()["version_suggestion"]
    assert (banner["reasons"], banner["text_overlap_estimated"]) == ("100% the same text", False)
    body = signed.client.get(f"/api/documents/{new}/compare", params={"with": old}).json()
    assert body["overview"] == "The text is the same in both files."


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
