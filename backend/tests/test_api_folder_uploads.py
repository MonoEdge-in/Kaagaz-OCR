"""The folder upload routes (#484) against PostgreSQL: the plan (names, the list's order, the
department rule, one upload sending and one plan at a time per organisation, the body cap, one
audit row with counts and ids only), registering files (idempotent, their folder and batch fixed,
the limits from registered sizes, a bad name failed) and reading progress. The limits and the
depth: test_folder_uploads_plan_db. Sending the files: test_api_folder_upload_files."""

import unicodedata
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.folder_upload_items import batch_for
from tests.folder_upload_support import path_key
from tests.test_api_support import CSRF, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
# The organisation's plan lock (db/folder_uploads.py), held here as another plan would hold it.
_PLAN_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.folder_plan:' || CAST(:c AS uuid)::text, 0))"
)

TREE: list[dict[str, Any]] = [
    {"name": "A", "parent": None},
    {"name": "B", "parent": 0},
    {"name": "C", "parent": 1},
]


def plan(signed: Signed, folders: list[dict[str, Any]] = TREE, **changes: Any) -> Any:
    body = {
        "id": str(uuid.uuid4()),
        "user_id": signed.user_id,
        "target": "folder",
        "folders": folders,
        "file_count": 3,
        "total_bytes": 3_000,
        "skipped_count": 1,
    } | changes
    return signed.client.post("/api/folder-uploads", json=body, headers=CSRF)


def register(signed: Signed, upload: str, items: list[dict[str, Any]]) -> Any:
    body = {"user_id": signed.user_id, "items": items}
    return signed.client.post(f"/api/folder-uploads/{upload}/items", json=body, headers=CSRF)


def item(path: str, folder: str, size: int = 1_000, **extra: Any) -> dict[str, Any]:
    return {
        "path_key": path_key(path),
        "name": path.rsplit("/", 1)[-1],
        "folder_id": folder,
        "size": size,
        **extra,
    }


def _error(reply: Any) -> tuple[int, str, str]:
    error = reply.json()["error"]
    return reply.status_code, error["code"], error["message"]


def test_a_plan_makes_the_folders_in_order_and_audits_counts_and_ids_only(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = plan(signed)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    folders = {f["id"]: f for f in signed.client.get("/api/folders").json()["items"]}
    assert [folders[i]["name"] for i in body["folder_ids"]] == ["A", "B", "C"]
    assert [folders[i]["parent_id"] for i in body["folder_ids"]] == [None, *body["folder_ids"][:2]]
    assert (body["root_folder_id"], body["root_name"], body["created"], body["merged"]) == (
        body["folder_ids"][0],
        "A",
        3,
        0,
    )
    rows = [row for row in api.audit_rows(signed.customer_id) if row[0] == "folder.upload"]
    assert len(rows) == 1
    _, target_type, target_id, details = rows[0]
    assert (target_type, target_id) == ("folder", body["root_folder_id"])
    assert details == {
        "folder_upload_id": body["id"],
        "files": 3,
        "bytes": 3_000,
        "folders_created": 3,
        "folders_merged": 0,
        "skipped": 1,
        "target": "folder",
    }


def test_a_new_department_is_an_admins_and_a_member_gets_403(db: Database) -> None:
    api = Api(db)
    member = api.new_signed()
    refused = plan(member, target="department")
    assert _error(refused) == (403, "forbidden", "Only an admin can add a department.")
    admin = api.admin_sign_in(member.customer_id)
    made = plan(admin, target="department")
    assert made.status_code == 200, made.text
    kinds = {f["id"]: f["kind"] for f in admin.client.get("/api/folders").json()["items"]}
    assert [kinds[i] for i in made.json()["folder_ids"]] == ["department", "custom", "custom"]


def test_a_bad_list_or_name_is_400_and_a_mac_name_is_kept_as_one(db: Database) -> None:
    signed = Api(db).new_signed()
    bad: list[list[dict[str, Any]]] = [
        [{"name": "A", "parent": None}, {"name": "B", "parent": 1}],  # its own parent
        [{"name": "A", "parent": None}, {"name": "B", "parent": None}],  # two roots
        [{"name": "A", "parent": 0}],
    ]
    for folders in bad:
        assert _error(plan(signed, folders))[:2] == (400, "bad_request")
    unsafe = (
        "line\u2028break",
        "para\u2029",
        "private\ue000",
        "tag\U000e0041",
        "unassigned\u0378",
        "\u061cx",
    )
    for name in (
        "a/b",
        "..",
        "a\\b",
        "evil\u202etxt.exe",
        "zero\u200bwidth",
        " ",
        "x" * 101,
        "bell\x07",
        *unsafe,
    ):
        assert _error(plan(signed, [{"name": name, "parent": None}]))[:2] == (400, "bad_folder_name"), name
    nfd = unicodedata.normalize("NFD", "Café")
    made = plan(signed, [{"name": nfd, "parent": None}, {"name": "मराठी\u200cफाइल\u200d", "parent": 0}])
    assert made.status_code == 200, made.text
    assert made.json()["root_name"] == "Café" == unicodedata.normalize("NFC", nfd)
    assert _error(plan(signed, total_bytes=20 * 1024**3 + 1))[:2] == (400, "bad_request")  # the 20 GB ceiling


def test_a_plan_over_8_mb_is_413_before_it_is_read(db: Database) -> None:
    signed = Api(db).new_signed()
    huge = b'{"names": "' + b"x" * (8 * 1024 * 1024) + b'"}'
    reply = signed.client.post(
        "/api/folder-uploads", content=huge, headers={**CSRF, "Content-Type": "application/json"}
    )
    assert reply.status_code == 413


def test_registering_twice_returns_the_same_items_and_never_moves_their_folder(db: Database) -> None:
    signed = Api(db).new_signed()
    planned = plan(signed).json()
    upload, (a, b, _c) = planned["id"], planned["folder_ids"]
    first = register(signed, upload, [item("A/one.pdf", a), item("A/B/two.pdf", b)])
    assert first.status_code == 200, first.text
    again = register(signed, upload, [item("A/B/two.pdf", a), item("A/one.pdf", a)])  # another folder
    one, two = first.json()["items"]
    assert again.json()["items"] == [two, one]
    assert (one["folder_id"], two["folder_id"], one["state"], one["seq"], two["seq"]) == (
        a,
        b,
        "waiting",
        0,
        1,
    )
    assert one["document"] is None


def test_past_the_planned_count_or_bytes_is_refused_and_a_folder_not_planned_is_400(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    planned = plan(signed).json()  # 3 files, 3,000 bytes
    upload, (a, _b, _c) = planned["id"], planned["folder_ids"]
    other = plan(signed).json()  # Changed on purpose (#484): planned before a file waits
    assert register(signed, upload, [item(f"A/{n}.pdf", a) for n in range(3)]).status_code == 200
    assert _error(register(signed, upload, [item("A/4.pdf", a)]))[:2] == (409, "too_many_files")
    assert _error(register(signed, other["id"], [item("A/x.pdf", a, 3_001)]))[:2] == (409, "too_large_upload")
    elsewhere = api.add_folder(signed.customer_id, signed.user_id, "Elsewhere")
    assert _error(register(signed, other["id"], [item("A/y.pdf", elsewhere)]))[:2] == (400, "bad_request")
    twice = [item("A/z.pdf", other["folder_ids"][0]), item("A/z.pdf", other["folder_ids"][0])]
    assert _error(register(signed, other["id"], twice))[:2] == (400, "bad_request")


def test_a_file_kaagaz_cant_take_is_registered_failed_with_its_reason(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    planned = plan(signed, file_count=3, total_bytes=10**9).json()
    a = planned["folder_ids"][0]
    items = [
        item("A/empty.pdf", a, 0),
        item("A/huge.pdf", a, api.settings.max_upload_bytes + 1),
        item("A/run.exe", a, 10, refused="unsupported_type"),
    ]
    rows = register(signed, planned["id"], items).json()["items"]
    assert [(row["state"], row["failure_code"]) for row in rows] == [
        ("failed", "empty_file"),
        ("failed", "too_large"),
        ("failed", "unsupported_type"),
    ]
    for told in ("too_large", "empty_file"):  # the server decides these itself
        reply = register(signed, planned["id"], [item(f"A/{told}.pdf", a, 10, refused=told)])
        assert _error(reply)[:2] == (400, "bad_request"), told


def test_an_unsafe_or_nfc_colliding_file_name_is_failed_bad_name_and_the_rest_register(db: Database) -> None:
    signed = Api(db).new_signed()
    planned = plan(signed, file_count=7, total_bytes=10**6).json()
    upload, (a, b, _c) = planned["id"], planned["folder_ids"]
    nfc, nfd = "Café.pdf", unicodedata.normalize("NFD", "Café.pdf")
    page = [
        item("A/ok.pdf", a),
        {**item("A/evil", a), "name": "evil\u202etxt.pdf"},
        item(f"A/{nfc}", a),
        item(f"A/{nfd}", a),  # the path before it, once NFC
        item(f"A/B/{nfd}", b),  # another folder: no clash
        item("A/मराठी\u200cफाइल\u200d.pdf", a),  # ZWNJ and ZWJ: Devanagari needs them
    ]
    reply = register(signed, upload, page)
    assert reply.status_code == 200, reply.text
    rows = reply.json()["items"]
    assert [(row["state"], row["failure_code"]) for row in rows] == [
        ("waiting", None),
        ("failed", "bad_name"),
        ("waiting", None),
        ("failed", "bad_name"),
        ("waiting", None),
        ("waiting", None),
    ]
    assert (rows[1]["name"], rows[3]["name"], rows[4]["name"]) == ("evil\ufffdtxt.pdf", nfc, nfc)
    later = register(signed, upload, [{**item("A/later", a), "name": nfd}])  # a registered one has it
    assert [(row["state"], row["failure_code"]) for row in later.json()["items"]] == [("failed", "bad_name")]


def test_a_new_upload_waits_while_another_of_the_organisation_still_has_files_waiting(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)
    first = plan(signed).json()
    register(signed, first["id"], [item("A/one.pdf", first["folder_ids"][0])])
    assert _error(plan(colleague)) == (
        409,
        "upload_in_progress",
        "Another folder upload is still sending in this organisation. Try again in a few minutes.",
    )
    assert plan(signed, id=first["id"]).status_code == 200  # a re-plan of the same upload
    assert plan(signed).status_code == 200  # the same user's new upload (production QA P1)
    assert plan(api.new_signed()).status_code == 200  # another organisation
    _owner(
        db,
        "UPDATE folder_upload_items SET state = 'failed', failure_code = 'too_large'"
        " WHERE folder_upload_id = :u",
        {"u": first["id"]},
    )
    assert plan(colleague).status_code == 200  # nothing waits any more


def test_an_upload_quiet_for_30_minutes_no_longer_blocks_the_organisation(db: Database) -> None:
    """Production QA P1: a stopped or abandoned upload's waiting files blocked every new folder
    upload of the organisation for ever. Only one active in the last 30 minutes blocks now."""
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)
    first = plan(signed).json()
    register(signed, first["id"], [item("A/one.pdf", first["folder_ids"][0])])
    assert _error(plan(colleague))[1] == "upload_in_progress"
    upload_at = "UPDATE folder_uploads SET updated_at = now() - CAST(:ago AS interval) WHERE id = :u"
    items_at = (
        "UPDATE folder_upload_items SET updated_at = now() - CAST(:ago AS interval)"
        " WHERE folder_upload_id = :u"
    )
    _owner(db, upload_at, {"u": first["id"], "ago": "29 minutes"})
    _owner(db, items_at, {"u": first["id"], "ago": "29 minutes"})
    assert _error(plan(colleague))[1] == "upload_in_progress"  # still active 29 minutes ago
    _owner(db, upload_at, {"u": first["id"], "ago": "31 minutes"})
    assert _error(plan(colleague))[1] == "upload_in_progress"  # a file of it moved 29 minutes ago
    _owner(db, items_at, {"u": first["id"], "ago": "31 minutes"})
    assert plan(colleague).status_code == 200


def test_a_plan_while_another_of_the_organisation_is_being_built_is_429_plan_busy(db: Database) -> None:
    signed = Api(db).new_signed()
    with db.customer(signed.customer_id) as conn:
        conn.execute(text(_PLAN_LOCK), {"c": signed.customer_id})
        busy = plan(signed)
    assert (busy.status_code, busy.headers["Retry-After"], busy.json()["error"]["code"]) == (
        429,
        "2",
        "plan_busy",
    )
    assert plan(signed).status_code == 200


def test_items_go_in_batches_of_max_files_per_batch(db: Database) -> None:
    upload = str(uuid.uuid4())
    assert batch_for(upload, 0, 100) == batch_for(upload, 99, 100) != batch_for(upload, 100, 100)
    assert batch_for(upload, 250, 100) == batch_for(upload, 200, 100)
    assert batch_for(upload, 250, 100) != batch_for(str(uuid.uuid4()), 250, 100)


def test_progress_counts_by_state_and_pages_the_items(db: Database) -> None:
    signed = Api(db).new_signed()
    planned = plan(signed, file_count=3, total_bytes=10**6).json()
    upload, a = planned["id"], planned["folder_ids"][0]
    register(signed, upload, [item("A/1.pdf", a), item("A/2.pdf", a), item("A/3.pdf", a, 0)])
    listed = signed.client.get("/api/folder-uploads").json()["items"]
    assert [(u["id"], u["waiting"], u["failed"], u["registered"], u["reading"]) for u in listed] == [
        (upload, 2, 1, 3, None)
    ]
    one = signed.client.get(f"/api/folder-uploads/{upload}").json()
    assert (one["root_name"], one["file_count"], one["skipped_count"], one["stored"], one["reading"]) == (
        "A",
        3,
        1,
        0,
        0,
    )
    assert (one["source_name"], one["parent_folder_id"], one["merge"]) == ("A", None, True)
    assert (listed[0]["source_name"], listed[0]["parent_folder_id"], listed[0]["merge"]) == ("A", None, True)
    page = signed.client.get(f"/api/folder-uploads/{upload}/items?view=waiting&limit=1").json()
    assert ([row["seq"] for row in page["items"]], page["next_after"]) == ([0], 0)
    rest = signed.client.get(f"/api/folder-uploads/{upload}/items?view=waiting&after=0&limit=1").json()
    assert [row["seq"] for row in rest["items"]] == [1]
    attention = signed.client.get(f"/api/folder-uploads/{upload}/items?view=attention").json()
    assert ([row["seq"] for row in attention["items"]], attention["next_after"]) == ([2], None)
