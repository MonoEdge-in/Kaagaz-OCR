"""File management (Phase 2 PRD 1) stage 3 against PostgreSQL: views and downloads (A1, A2),
each row's folder and family (A3), the three timelines and the admin's filters (A5), the
export (A6), keyword search with My words and the list's filters (Q1 to Q3), and the
questions about a document (P2). Section 10: 20 opens give one view row, My words are per
person, and the export of 50,000 rows streams and is logged. Two customers:
test_isolation_file_activity.py.
"""

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from kaagaz.api import views
from kaagaz.api.routers import admin
from kaagaz.db import audit
from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_indexing_db import FakeModel
from tests.test_search_db import add_pieces
from tests.test_user_words_db import added

pytestmark = pytest.mark.db

_PLACE = text(
    "SELECT target_id, folder_id::text, family_id::text FROM audit_log"
    " WHERE customer_id = :c AND action = :a ORDER BY id"
)
_ALLOWED = {action: set(keys) | {"folder_path", "parent_path", "from_name", "to_name"}
            for action, keys in audit.SHOWN_DETAILS.items()}  # fmt: skip


def place(db: Database, customer: str, action: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(_PLACE, {"c": customer, "a": action}).all()]


def every(signed: Signed, path: str, **params: Any) -> list[dict[str, Any]]:
    """Every page of a timeline, following next_cursor."""
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        reply = signed.client.get(path, params={**params, **({} if cursor is None else {"cursor": cursor})})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        items += body["items"]
        if (cursor := body["next_cursor"]) is None:
            return items


def only_file_actions(items: list[dict[str, Any]]) -> None:
    """The security review's rule for what members see."""
    for item in items:
        assert item["action"].split(".")[0] in audit.SHOWN_KINDS, item["action"]
        assert set(item["details"]) <= _ALLOWED.get(item["action"], set()), item


def family(api: Api, signed: Signed, folder: str | None = None) -> tuple[str, str]:
    c, u = signed.customer_id, signed.user_id
    first = api.add_document(c, uploaded_by=u, is_current=False, folder_id=folder, owner_id=u)
    second = api.add_document(
        c, uploaded_by=u, name="v2.pdf", family_id=first, version_no=2, folder_id=folder, owner_id=u
    )
    return first, second


# --- A1, A2, A3 -------------------------------------------------------------------------


def test_a1_section_10_twenty_opens_in_ten_minutes_give_one_view_row(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)
    document = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
    for _ in range(20):
        assert signed.client.get(f"/api/documents/{document}").status_code == 200
    assert len(place(db, signed.customer_id, "document.view")) == 1
    assert colleague.client.get(f"/api/documents/{document}").status_code == 200  # per person
    assert len(place(db, signed.customer_id, "document.view")) == 2
    with db.engine.begin() as conn:  # 31 minutes pass (the owner: the app can't change the log)
        conn.execute(
            text(
                "UPDATE audit_log SET created_at = created_at - interval '31 minutes'"
                " WHERE customer_id = :c AND action = 'document.view'"
            ),
            {"c": signed.customer_id},
        )
    assert signed.client.get(f"/api/documents/{document}").status_code == 200
    assert len(place(db, signed.customer_id, "document.view")) == 3


def test_a2_only_the_download_button_logs_and_the_file_is_an_attachment_with_its_name(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id, name="Sale Deed Shinde.pdf")
    viewer = signed.client.get(f"/api/documents/{document}/file")
    assert viewer.status_code == 200
    assert place(db, signed.customer_id, "document.download") == []
    download = signed.client.get(f"/api/documents/{document}/file", params={"download": "1"})
    assert download.status_code == 200 and download.content == viewer.content
    disposition = download.headers["content-disposition"]
    assert (
        disposition.startswith("attachment;") and "filename*=UTF-8''Sale%20Deed%20Shinde.pdf" in disposition
    )
    assert [row[0] for row in place(db, signed.customer_id, "document.download")] == [document]


def test_a3_each_row_keeps_the_documents_folder_and_family_or_the_folder(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "Land records")
    first, second = family(api, signed, folder)
    reply = signed.client.patch(f"/api/documents/{second}", json={"display_name": "v2 new.pdf"}, headers=CSRF)
    assert reply.status_code == 200
    renamed = signed.client.patch(f"/api/folders/{folder}", json={"name": "Land"}, headers=CSRF)
    assert renamed.status_code == 200
    assert place(db, signed.customer_id, "document.rename") == [(second, folder, first)]
    assert place(db, signed.customer_id, "folder.rename") == [(folder, folder, None)]
    assert place(db, signed.customer_id, "auth.login")[0][1:] == (None, None)


# --- A5: the timelines ------------------------------------------------------------------


def test_a5_the_document_timeline_holds_every_version_and_only_file_actions(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, second = family(api, signed)
    signed.client.get(f"/api/documents/{first}")
    signed.client.get(f"/api/documents/{second}")
    signed.client.patch(f"/api/documents/{second}", json={"sensitivity": "restricted"}, headers=CSRF)
    with db.customer(signed.customer_id) as conn:  # a row with details members never see
        audit.record(
            conn,
            signed.customer_id,
            actor_user_id=signed.user_id,
            action="document.upload",
            target_type="document",
            target_id=second,
            details={"read_model": "google/x", "family_id": first},
        )
    items = every(signed, f"/api/documents/{first}/activity", limit=2)
    assert [item["action"] for item in items] == [
        "document.upload",
        "document.label_change",
        "document.view",
        "document.view",
    ]
    only_file_actions(items)
    upload, label = items[0], items[1]
    assert upload["details"] == {}
    assert label["details"] == {"from": "internal", "to": "restricted"}
    assert label["document"] == {"id": second, "name": "v2.pdf", "version_no": 2, "deleted": False}
    assert label["actor_name"] and label["family_id"] == first
    assert len({item["id"] for item in items}) == 4  # the cursor never repeats a row


def test_a5_the_folder_timeline_holds_the_folder_and_everything_below_it(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    c, u = signed.customer_id, signed.user_id
    top = api.add_folder(c, u, "Land & Legal")
    inner = api.add_folder(c, u, "Land records", top)
    elsewhere = api.add_folder(c, u, "Bills")
    inside = api.add_document(c, uploaded_by=u, folder_id=inner)
    outside = api.add_document(c, uploaded_by=u, folder_id=elsewhere)
    signed.client.get(f"/api/documents/{inside}")
    signed.client.get(f"/api/documents/{outside}")
    signed.client.patch(f"/api/folders/{inner}", json={"name": "Land"}, headers=CSRF)
    items = every(signed, f"/api/folders/{top}/activity")
    assert [(item["action"], item["target_id"]) for item in items] == [
        ("folder.rename", inner),
        ("document.view", inside),
    ]
    assert items[1]["folder"] == {"id": inner, "path": "Land & Legal / Land"}
    only_file_actions(items)
    assert every(signed, f"/api/folders/{inner}/activity") == items
    assert signed.client.get(f"/api/folders/{uuid.uuid4()}/activity").status_code == 404


def test_a5_the_dashboard_and_the_timelines_never_show_people_sign_ins_or_settings(db: Database) -> None:
    """Security review (19 Oct 2026): a member sees document, version and folder actions
    only, and only the allowed details."""
    api = Api(db)
    owner = api.new_admin()
    member = api.sign_in(owner.customer_id)
    owner.client.patch("/api/admin/settings", json={"max_upload_mb": 2}, headers=CSRF)
    document = api.add_document(owner.customer_id, uploaded_by=member.user_id)
    with db.customer(owner.customer_id) as conn:
        for action, target_type, target in (
            ("user.create", "user", member.user_id),
            ("document.move", "document", document),
        ):
            audit.record(
                conn,
                owner.customer_id,
                actor_user_id=owner.user_id,
                action=action,
                target_type=target_type,
                target_id=target,
                details={"role": "admin", "folder_id": None, "email": "x@example.test"},
            )
    member.client.get(f"/api/documents/{document}")
    feed = member.client.get("/api/dashboard").json()["activity"]
    assert [item["action"] for item in feed] == ["document.view", "document.move"]
    only_file_actions(feed)
    assert feed[1]["details"] == {"folder_id": None}
    timeline = every(member, f"/api/documents/{document}/activity")
    assert [item["action"] for item in timeline] == ["document.view", "document.move"]
    full = every(owner, "/api/admin/audit")  # the admin's log is the whole log
    assert {"user.create", "auth.login", "settings.update"} <= {item["action"] for item in full}


def test_a5_the_admin_log_filters_by_person_folder_file_action_and_day(db: Database) -> None:
    api = Api(db)
    owner = api.new_admin()
    member = api.sign_in(owner.customer_id)
    c, u = owner.customer_id, member.user_id
    top = api.add_folder(c, u, "Land & Legal")
    inner = api.add_folder(c, u, "Land records", top)
    first, second = family(api, member, inner)
    other = api.add_document(c, uploaded_by=u)
    for document in (first, second, other):
        member.client.get(f"/api/documents/{document}")
    owner.client.get(f"/api/documents/{other}")

    def ids(**params: Any) -> list[str]:
        return [
            item["target_id"] for item in every(owner, "/api/admin/audit", action="document.view", **params)
        ]

    assert ids() == [other, other, second, first]
    assert ids(user_id=u) == [other, second, first]
    assert ids(folder_id=top) == [second, first]  # with its subfolders
    assert ids(document_id=first) == [second, first]  # every version of the family
    today = datetime.now(ZoneInfo(api.settings.timezone)).date()
    assert ids(**{"from": today.isoformat(), "to": today.isoformat()}) == [other, other, second, first]
    assert ids(**{"from": today.replace(year=today.year + 1).isoformat()}) == []
    body = owner.client.get("/api/admin/audit", params={"action": "document.view", "limit": 1}).json()
    assert (body["total"], len(body["items"])) == (4, 1) and body["next_cursor"]
    assert owner.client.get("/api/admin/audit", params={"action": "Drop table"}).status_code == 400
    assert owner.client.get("/api/admin/audit", params={"cursor": "x"}).status_code == 400
    assert member.client.get("/api/admin/audit").status_code == 403


def test_a5_a_deleted_document_shows_as_a_deleted_document(db: Database) -> None:
    api = Api(db)
    owner = api.new_admin()
    with db.customer(owner.customer_id) as conn:
        audit.record(
            conn,
            owner.customer_id,
            actor_user_id=owner.user_id,
            action="document.delete",
            target_type="document",
            target_id=str(uuid.uuid4()),
        )
    (row,) = every(owner, "/api/admin/audit", action="document.delete")
    assert (row["document"]["name"], row["document"]["deleted"]) == ("a deleted document", True)


# --- A6: the export ---------------------------------------------------------------------


def _export(signed: Signed, **params: Any) -> list[str]:
    reply = signed.client.get("/api/admin/audit/export", params=params)
    assert reply.status_code == 200, reply.text
    assert reply.headers["content-type"].startswith("text/csv")
    return reply.text.splitlines()


def counted_pages(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The page size of every page the export reads, in order."""
    pages: list[int] = []

    def counted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pages.append(kwargs["limit"])
        return views.timeline(*args, **kwargs)

    monkeypatch.setattr(admin, "timeline", counted)
    return pages


def test_a6_the_export_streams_page_by_page_and_is_logged(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    owner = api.new_admin()
    member = api.sign_in(owner.customer_id)
    for _ in range(10):
        member.client.get(f"/api/documents/{api.add_document(owner.customer_id, name='=cmd.pdf')}")
    monkeypatch.setattr(admin, "AUDIT_EXPORT_PAGE", 3)
    pages = counted_pages(monkeypatch)
    lines = _export(owner, action="document.view")
    assert lines[0] == "When,Who,Action,Document,Folder,Details"
    assert len(lines) == 11 and pages == [3, 3, 3, 3]  # 3, 3, 3 and the last 1
    assert all(",document.view,'=cmd.pdf," in line for line in lines[1:])  # never a formula
    (logged,) = place(db, owner.customer_id, "audit.export")
    assert logged[0] == owner.customer_id
    assert member.client.get("/api/admin/audit/export").status_code == 403


def test_a6_section_10_exporting_50000_rows_streams_finishes_and_is_logged(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    owner = api.new_admin()
    with db.engine.begin() as conn:  # the owner: 50,000 rows in one statement
        conn.execute(
            text(
                "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id)"
                " SELECT CAST(:c AS uuid), CAST(:u AS uuid), 'document.view', 'document',"
                " gen_random_uuid()::text FROM generate_series(1, 50000)"
            ),
            {"c": owner.customer_id, "u": owner.user_id},
        )
    pages = counted_pages(monkeypatch)
    lines = _export(owner, action="document.view")
    assert len(lines) == 50001 and pages == [1000] * 50
    assert all(line.endswith(",document.view,a deleted document,,") for line in lines[1:])
    assert len(place(db, owner.customer_id, "audit.export")) == 1


# --- Q1 to Q3: search ---------------------------------------------------------------------


def _found(signed: Signed, **params: Any) -> dict[str, Any]:
    reply = signed.client.get("/api/search", params=params)
    assert reply.status_code == 200, reply.text
    body: dict[str, Any] = reply.json()
    return body


def test_q1_search_is_keyword_only_with_no_model_call_and_no_story_matches(db: Database) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    add_pieces(db, signed.customer_id, document, [("Rent agreement with Shinde", None)])
    body = _found(signed, q="agrement shinde")  # words and typos still match
    assert [found["id"] for found in body["documents"]] == [document]
    assert (body["story_matches"], body["understood_words"], model.calls) == ([], [], [])


def test_q2_section_10_my_words_are_used_for_their_owner_only(db: Database) -> None:
    api = Api(db)
    priya = api.new_signed()
    colleague = api.sign_in(priya.customer_id)
    document = api.add_document(priya.customer_id)
    add_pieces(db, priya.customer_id, document, [("Warehouses at Bhiwandi", None)])
    added(db, priya.customer_id, priya.user_id, "khilwa", "warehouse")
    mine = _found(priya, q="khilwa")
    assert [found["id"] for found in mine["documents"]] == [document]
    assert mine["understood_words"] == [{"word": "khilwa", "meaning": "warehouse"}]
    theirs = _found(colleague, q="khilwa")
    assert (theirs["documents"], theirs["understood_words"]) == ([], [])


def test_q3_search_takes_the_document_lists_filters(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    anita, _ = api.add_user(signed.customer_id, "member")
    c, u = signed.customer_id, signed.user_id
    department = api.add_folder(c, u, "Land & Legal")
    land = api.add_document(
        c, uploaded_by=u, owner_id=anita, sensitivity="confidential", folder_id=department
    )
    deed = api.add_document(c, uploaded_by=u, owner_id=u, sensitivity="restricted")
    for document in (land, deed):
        add_pieces(db, c, document, [("Survey 44 Wakad", None)])

    def ids(**params: Any) -> set[str]:
        return {found["id"] for found in _found(signed, q="Wakad", **params)["documents"]}

    assert ids() == {land, deed}
    assert ids(sensitivity="restricted") == {deed}
    assert ids(sensitivity=["restricted", "confidential"]) == {land, deed}
    assert ids(owner_id=anita) == {land}
    assert ids(department_id=department) == {land}
    assert ids(department_id=str(uuid.uuid4())) == set()
    assert ids(owner_id=anita, document_ids=[deed]) == set()
    assert signed.client.get("/api/search", params={"q": "Wakad", "sensitivity": "secret"}).status_code == 400


# --- P2: questions ------------------------------------------------------------------------


def ask(db: Database, signed: Signed, question: str, *, scope: list[str] | None, cited: list[str]) -> None:
    """One question and its answer, written as the chat writes them (one transaction)."""
    c, u = signed.customer_id, signed.user_id
    with db.customer(c) as conn:
        conversation = chat_sql.create_conversation(conn, c, u, question)
        chat_sql.add_message(
            conn,
            c,
            conversation,
            role="user",
            message_text=question,
            answer=None,
            scope=None if scope is None else {"document_ids": scope, "folder_id": None},
        )
        answer = {"status": "answered", "citations": [{"n": 1, "document_id": d} for d in cited]}
        chat_sql.add_message(conn, c, conversation, role="assistant", message_text="A", answer=answer)


def test_p2_this_persons_questions_scoped_to_or_citing_any_version_newest_first(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)
    first, second = family(api, signed)
    other = api.add_document(signed.customer_id)
    ask(db, signed, "Who sold it?", scope=[first], cited=[])
    ask(db, signed, "What is the area?", scope=None, cited=[other, second])
    ask(db, signed, "Unrelated?", scope=[other], cited=[other])
    ask(db, colleague, "Mine?", scope=[second], cited=[second])
    reply = signed.client.get(f"/api/documents/{second}/questions")
    assert reply.status_code == 200, reply.text
    items = reply.json()["items"]
    assert [item["question"] for item in items] == ["What is the area?", "Who sold it?"]
    assert items[0]["answer"]["citations"][1]["document_id"] == second and items[0]["asked_at"]
    assert [
        i["question"] for i in colleague.client.get(f"/api/documents/{first}/questions").json()["items"]
    ] == ["Mine?"]


def test_p2_a_cited_document_in_trash_shows_no_snippet(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, _second = family(api, signed)
    other = api.add_document(signed.customer_id)
    ask(db, signed, "What is the area?", scope=[first], cited=[other])
    with db.customer(signed.customer_id) as conn:  # the citation's words, as the chat saves them
        conn.execute(
            text(
                "UPDATE messages SET answer = jsonb_set(answer, '{citations,0,snippet}', '\"2 acres\"')"
                " WHERE customer_id = :c AND answer IS NOT NULL"
            ),
            {"c": signed.customer_id},
        )
    assert signed.client.delete(f"/api/documents/{other}", headers=CSRF).status_code == 204
    cited = signed.client.get(f"/api/documents/{first}/questions").json()["items"][0]["answer"]["citations"][0]
    assert cited["snippet"] is None and cited["source_state"] == "in_trash"
