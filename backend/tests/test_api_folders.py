"""The folder routes (PRD 1 F1) against a real database.

The tree rules themselves are tested on the helpers (test_folders_db.py);
these tests check what the API adds: the reply shapes, the error codes and
words, the name rule, "a missing key means don't move", and the audit rows.
"""

from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.db.engine import Database
from tests.db_support import insert_document
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"


def _create(signed: Signed, name: str, parent_id: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    reply: httpx.Response = signed.client.post("/api/folders", json=body, headers=CSRF)
    return reply


def _patch(signed: Signed, folder_id: str, body: dict[str, Any]) -> httpx.Response:
    reply: httpx.Response = signed.client.patch(f"/api/folders/{folder_id}", json=body, headers=CSRF)
    return reply


def _chain(api: Api, signed: Signed, levels: int, prefix: str = "L") -> list[str]:
    """Folders nested ``levels`` deep, named L1, L2, ..., the top one first."""
    ids: list[str] = []
    for level in range(1, levels + 1):
        ids.append(
            api.add_folder(signed.customer_id, signed.user_id, f"{prefix}{level}", ids[-1] if ids else None)
        )
    return ids


def _error(reply: httpx.Response) -> dict[str, str]:
    error: dict[str, str] = reply.json()["error"]
    return error


# --- create and list --------------------------------------------------------------------


def test_a_member_creates_folders_and_lists_them_flat_by_name(db: Database) -> None:
    api = Api(db)
    member = api.new_signed("member")

    suppliers = _create(member, "Suppliers")
    assert suppliers.status_code == 201
    folder = suppliers.json()
    # Changed on purpose (PRD 1, 0041): a folder says its kind and order.
    assert set(folder) == {"id", "name", "parent_id", "created_at", "kind", "sort_order"}
    assert (folder["name"], folder["parent_id"]) == ("Suppliers", None)
    year = _create(member, "2026", parent_id=folder["id"]).json()
    assert year["parent_id"] == folder["id"]
    other = _create(member, "banks").json()

    listed = member.client.get("/api/folders")
    assert listed.status_code == 200
    assert listed.json() == {"items": [year, other, folder]}  # 2026, banks, Suppliers: lower(name)


def test_the_name_is_trimmed_and_must_be_1_to_100_characters(db: Database) -> None:
    signed = Api(db).new_signed()
    assert _create(signed, "  GST 2026/27\t").json()["name"] == "GST 2026/27"
    assert _create(signed, "a" * 100).status_code == 201
    for name in ("", "   ", "\x00", "a" * 101):
        reply = _create(signed, name)
        assert reply.status_code == 400, name
        assert _error(reply) == {
            "code": "bad_request",
            "message": "Enter a folder name of 1 to 100 characters.",
        }


def test_a_sibling_with_the_same_name_in_any_case_is_409_conflict(db: Database) -> None:
    signed = Api(db).new_signed()
    top = _create(signed, "GST").json()
    clash = _create(signed, "gst")
    assert clash.status_code == 409
    assert _error(clash) == {"code": "conflict", "message": "That already exists."}
    assert _create(signed, "GST", parent_id=top["id"]).status_code == 201  # not a sibling
    assert [item["name"] for item in signed.client.get("/api/folders").json()["items"]] == ["GST", "GST"]


def test_create_under_an_unknown_parent_is_404(db: Database) -> None:
    signed = Api(db).new_signed()
    reply = _create(signed, "Inside", parent_id=UNKNOWN)
    assert reply.status_code == 404
    assert _error(reply)["code"] == "not_found"
    assert signed.client.get("/api/folders").json() == {"items": []}


# Changed on purpose (#484, 3 Nov 2026): the 101st level, was the 6th.
def test_a_101st_level_is_409_folder_too_deep(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    levels = _chain(api, signed, 100)
    reply = _create(signed, "L101", parent_id=levels[-1])
    assert reply.status_code == 409
    assert _error(reply) == {"code": "folder_too_deep", "message": "Folders can be at most 100 levels deep."}


# --- rename and move --------------------------------------------------------------------


def test_rename_keeps_the_parent_when_parent_id_is_left_out(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    top, child = _chain(api, signed, 2)
    reply = _patch(signed, child, {"name": "  Renamed "})
    assert reply.status_code == 200
    body = reply.json()
    assert (body["id"], body["name"], body["parent_id"]) == (child, "Renamed", top)


def test_an_explicit_null_parent_moves_to_the_top_level(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, child = _chain(api, signed, 2)
    reply = _patch(signed, child, {"parent_id": None})
    assert reply.status_code == 200
    assert (reply.json()["name"], reply.json()["parent_id"]) == ("L2", None)


def test_a_move_can_also_rename(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, second = (api.add_folder(signed.customer_id, signed.user_id, name) for name in ("A", "B"))
    reply = _patch(signed, second, {"name": "B moved", "parent_id": first})
    assert reply.status_code == 200
    assert (reply.json()["name"], reply.json()["parent_id"]) == ("B moved", first)


def test_a_move_into_itself_or_its_own_folder_is_409_folder_cycle(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    top, child = _chain(api, signed, 2)
    for target in (top, child):
        reply = _patch(signed, top, {"parent_id": target})
        assert reply.status_code == 409
        assert _error(reply) == {
            "code": "folder_cycle",
            "message": "A folder can't go inside itself or one of its own folders.",
        }
    assert signed.client.get("/api/folders").json()["items"][0]["parent_id"] is None


# Changed on purpose (#484, 3 Nov 2026): past 100 levels, was 5.
def test_a_move_that_goes_past_100_levels_is_409_folder_too_deep(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    deep = _chain(api, signed, 99)
    subtree = _chain(api, signed, 2, prefix="S")  # under level 99 it would need levels 100 and 101
    reply = _patch(signed, subtree[0], {"parent_id": deep[-1]})
    assert reply.status_code == 409
    assert _error(reply)["code"] == "folder_too_deep"


def test_rename_or_move_onto_a_taken_name_is_409_conflict(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    top = api.add_folder(signed.customer_id, signed.user_id, "Top")
    api.add_folder(signed.customer_id, signed.user_id, "Bills", top)
    loose = api.add_folder(signed.customer_id, signed.user_id, "bills")
    assert _error(_patch(signed, loose, {"parent_id": top}))["code"] == "conflict"
    assert _error(_patch(signed, top, {"name": "BILLS"}))["code"] == "conflict"


def test_patch_refuses_an_empty_body_a_blank_name_and_unknown_fields(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "GST")
    for body in ({}, {"name": " "}, {"name": None}, {"colour": "red"}):
        assert _patch(signed, folder, body).status_code == 400, body


def test_rename_or_move_of_an_unknown_folder_or_to_an_unknown_parent_is_404(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "GST")
    assert _patch(signed, UNKNOWN, {"name": "X"}).status_code == 404
    assert _patch(signed, UNKNOWN, {"parent_id": None}).status_code == 404
    assert _patch(signed, folder, {"parent_id": UNKNOWN}).status_code == 404
    assert signed.client.patch("/api/folders/not-a-uuid", json={"name": "X"}, headers=CSRF).status_code == 400


# --- delete -----------------------------------------------------------------------------


def test_delete_of_an_empty_folder_is_204(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "GST")
    assert signed.client.delete(f"/api/folders/{folder}", headers=CSRF).status_code == 204
    assert signed.client.get("/api/folders").json() == {"items": []}
    assert signed.client.delete(f"/api/folders/{folder}", headers=CSRF).status_code == 404


def test_delete_of_a_folder_with_a_folder_or_a_document_inside_is_409_folder_not_empty(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    parent, _ = _chain(api, signed, 2)
    holder = api.add_folder(signed.customer_id, signed.user_id, "Holder")
    with db.customer(signed.customer_id) as conn:
        insert_document(conn, signed.customer_id, folder_id=holder)
    for folder in (parent, holder):
        reply = signed.client.delete(f"/api/folders/{folder}", headers=CSRF)
        assert reply.status_code == 409
        assert _error(reply) == {
            "code": "folder_not_empty",
            "message": "This folder isn't empty. Move or delete what is inside it first.",
        }
    assert len(signed.client.get("/api/folders").json()["items"]) == 3


# --- audit and access -------------------------------------------------------------------


def test_every_folder_change_is_audited_with_ids_only(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    top = _create(signed, "Suppliers secret").json()["id"]
    child = _create(signed, "Child secret", parent_id=top).json()["id"]
    _patch(signed, child, {"name": "Renamed secret"})
    _patch(signed, child, {"parent_id": None})
    signed.client.delete(f"/api/folders/{child}", headers=CSRF)

    rows = [row for row in api.audit_rows(signed.customer_id) if row[0].startswith("folder.")]
    assert rows == [
        ("folder.create", "folder", top, {"parent_id": None}),
        ("folder.create", "folder", child, {"parent_id": top}),
        ("folder.rename", "folder", child, {}),
        ("folder.move", "folder", child, {"parent_id": None}),
        ("folder.delete", "folder", child, {}),
    ]
    assert "secret" not in repr(api.audit_rows(signed.customer_id))


def test_a_refused_change_writes_no_audit_row(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _create(signed, "GST")
    assert _create(signed, "GST").status_code == 409
    assert [row[0] for row in api.audit_rows(signed.customer_id) if row[0].startswith("folder.")] == [
        "folder.create"
    ]


def test_folders_need_a_session_and_writes_need_the_csrf_header(db: Database) -> None:
    api = Api(db)
    anonymous = api.client()
    assert anonymous.get("/api/folders").status_code == 401
    assert anonymous.post("/api/folders", json={"name": "X"}, headers=CSRF).status_code == 401
    signed = api.new_signed()
    reply = signed.client.post("/api/folders", json={"name": "X"})
    assert reply.status_code == 403
    assert _error(reply)["code"] == "csrf"
