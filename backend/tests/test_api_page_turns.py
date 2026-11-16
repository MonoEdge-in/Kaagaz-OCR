"""Page turns saved for everyone (PRD 7 section 14 step 2, #600): one person saves, everyone who
can see the file sees the turns in its detail and the review queue; per-page changes only (back as
scanned is turn 0 for each page, never a whole-file wipe), one audit row per save shown in the file's
activity; each refusal; saves at the same moment; each version keeps its own turns through a new
version, Join, Make current and Separate. A hidden file answers as a random id:
test_isolation_access_views (``_ROUTES``) and test_isolation_page_turns_db."""

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import trash
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_versions_db import NOW, _accept, _satbaras, _spot

pytestmark = pytest.mark.db


def _save(signed: Signed, document: str, *changes: tuple[int, int], user_id: Any = "me") -> Any:
    body = {
        "changes": [{"page_no": page, "turn": turn} for page, turn in changes],
        "user_id": signed.user_id if user_id == "me" else user_id,
    }
    return signed.client.patch(f"/api/documents/{document}/page-turns", json=body, headers=CSRF)


def _turns(signed: Signed, document: str) -> list[dict[str, int]]:
    reply = signed.client.get(f"/api/documents/{document}")
    assert reply.status_code == 200, reply.text
    turns: list[dict[str, int]] = reply.json()["page_turns"]
    return turns


def _two(api: Api, *, pages: int = 10) -> tuple[Signed, Signed, str]:
    """Two members of one organisation and a file of ``pages`` pages both can see."""
    a = api.new_signed()
    b = api.sign_in(a.customer_id)
    return a, b, api.add_document(a.customer_id, uploaded_by=a.user_id, page_count=pages)


def test_one_person_saves_and_everyone_sees_the_turns(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    assert _turns(b, document) == []
    reply = _save(a, document, (3, 90), (1, 270))
    assert reply.status_code == 200, reply.text
    expected = [{"page_no": 1, "turn": 270}, {"page_no": 3, "turn": 90}]
    assert reply.json() == {"page_turns": expected}
    assert _turns(b, document) == expected


def test_a_save_changes_only_its_pages_and_turn_0_removes_one(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    _save(a, document, (1, 90), (2, 180))
    reply = _save(b, document, (2, 0), (5, 270))  # b's older screen never names page 1
    assert reply.json()["page_turns"] == [{"page_no": 1, "turn": 90}, {"page_no": 5, "turn": 270}]


def test_back_as_scanned_is_turn_0_per_page_and_each_save_is_one_activity_row(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    _save(a, document, (1, 90), (2, 180), (3, 0))
    reply = _save(b, document, (1, 0), (2, 0))
    assert (reply.status_code, reply.json()) == (200, {"page_turns": []})
    assert _turns(a, document) == []
    rows = [row for row in api.audit_rows(a.customer_id) if row[0].startswith("document.page_turns")]
    assert rows == [
        ("document.page_turns", "document", document, {"pages": 3, "turned": 2}),
        ("document.page_turns", "document", document, {"pages": 2, "turned": 0}),
    ]
    items = a.client.get(f"/api/documents/{document}/activity").json()["items"]
    shown = [(item["action"], item["details"]) for item in items if item["action"] == "document.page_turns"]
    assert shown == [
        ("document.page_turns", {"pages": 2, "turned": 0}),
        ("document.page_turns", {"pages": 3, "turned": 2}),
    ]


def test_there_is_no_whole_file_delete(db: Database) -> None:
    """The lead's decision on the review: no write takes away turns the person did not name."""
    api = Api(db)
    a, _, document = _two(api)
    _save(a, document, (1, 90))
    reply = a.client.delete(f"/api/documents/{document}/page-turns?user_id={a.user_id}", headers=CSRF)
    assert reply.status_code == 405
    assert _turns(a, document) == [{"page_no": 1, "turn": 90}]


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [{"page_no": 1, "turn": 45}],
        [{"page_no": 1, "turn": 360}],
        [{"page_no": 1, "turn": "90"}],
        [{"page_no": 1, "turn": 90.0}],
        [{"page_no": "1", "turn": 90}],
        [{"page_no": 0, "turn": 90}],
        [{"page_no": 1, "turn": 90}, {"page_no": 1, "turn": 180}],
        [{"page_no": 1, "turn": 90, "extra": True}],
        [{"page_no": 11, "turn": 90}],  # past the file's 10 pages
    ],
)
def test_a_bad_body_is_400_and_saves_nothing(db: Database, changes: list[dict[str, Any]]) -> None:
    api = Api(db)
    a, _, document = _two(api)
    reply = a.client.patch(f"/api/documents/{document}/page-turns", json={"changes": changes}, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request"), reply.text
    assert _turns(a, document) == []


def test_a_page_past_the_end_names_it(db: Database) -> None:
    api = Api(db)
    a, _, document = _two(api, pages=4)
    reply = _save(a, document, (2, 90), (9, 90))
    assert reply.json()["error"]["message"] == "Page 9 is not in this file."


def test_more_changes_than_a_file_may_have_pages_is_400(db: Database) -> None:
    api = Api(db)
    a, _, document = _two(api)
    too_many = [(page, 90) for page in range(1, api.settings.max_pages + 2)]
    assert _save(a, document, *too_many).status_code == 400


def test_a_file_whose_pages_are_not_counted_yet_is_409_not_ready(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, status="clean", page_count=None)
    reply = _save(a, document, (1, 90))
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "not_ready")
    assert (
        reply.json()["error"]["message"]
        == "Kaagaz is still counting this file's pages. Try again in a minute."
    )


@pytest.mark.parametrize("status", ["failed", "rejected"])
def test_a_file_that_could_not_be_read_says_its_pages_cannot_be_turned(db: Database, status: str) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, status=status, page_count=None)
    reply = _save(a, document, (1, 90))
    assert (reply.status_code, reply.json()["error"]["message"]) == (
        409,
        "Kaagaz couldn't read this file's pages, so its pages can't be turned.",
    )


def test_a_file_in_trash_is_409_in_trash_and_keeps_its_turns(db: Database) -> None:
    api = Api(db)
    a, _, document = _two(api)
    _save(a, document, (1, 90))
    with db.customer(a.customer_id) as conn:
        assert trash.trash_document(conn, a.customer_id, document, by=a.user_id, now=NOW)
    reply = _save(a, document, (2, 90))
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "in_trash")
    with db.customer(a.customer_id) as conn:
        trash.restore_document(conn, a.customer_id, document, by=a.user_id)
    assert _turns(a, document) == [{"page_no": 1, "turn": 90}]


def test_another_person_signed_in_since_is_409_session_changed(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    reply = _save(a, document, (1, 90), user_id=b.user_id)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "session_changed")
    assert _turns(a, document) == []


def test_another_organisations_file_is_404(db: Database) -> None:
    api = Api(db)
    a, _, document = _two(api)
    _save(a, document, (1, 90))
    other = api.new_signed()
    reply = _save(other, document, (1, 180))
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert _turns(a, document) == [{"page_no": 1, "turn": 90}]


def _at_once(document: str, *saves: tuple[Signed, tuple[tuple[int, int], ...]]) -> list[int]:
    """Each person's save sent at the same moment, from its own thread; their status codes."""
    start = threading.Barrier(len(saves))
    codes = [0] * len(saves)

    def save(index: int, signed: Signed, changes: tuple[tuple[int, int], ...]) -> None:
        start.wait()
        codes[index] = _save(signed, document, *changes).status_code

    threads = [threading.Thread(target=save, args=(i, *each)) for i, each in enumerate(saves)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return codes


def test_two_saves_at_once_on_different_pages_both_keep_their_turn(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    assert _at_once(document, (a, ((3, 90),)), (b, ((7, 90),))) == [200, 200]
    assert _turns(a, document) == [{"page_no": 3, "turn": 90}, {"page_no": 7, "turn": 90}]


def test_two_saves_at_once_on_the_same_pages_queue_and_one_wins_on_both(db: Database) -> None:
    """They queue on the family lock: never a 500 (no clash on the key), never a mix."""
    api = Api(db)
    a, b, document = _two(api)
    assert _at_once(document, (a, ((3, 90), (4, 90))), (b, ((3, 270), (4, 270)))) == [200, 200]
    assert _turns(a, document) in (
        [{"page_no": 3, "turn": 90}, {"page_no": 4, "turn": 90}],
        [{"page_no": 3, "turn": 270}, {"page_no": 4, "turn": 270}],
    )


def test_a_save_while_someone_removes_a_turn_on_another_page_keeps_both(db: Database) -> None:
    api = Api(db)
    a, b, document = _two(api)
    _save(a, document, (2, 180))
    assert _at_once(document, (a, ((2, 0),)), (b, ((5, 90),))) == [200, 200]
    assert _turns(a, document) == [{"page_no": 5, "turn": 90}]


def test_the_review_queue_carries_the_pages_turn(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document = api.add_document(reviewer.customer_id, uploaded_by=reviewer.user_id, page_count=2)
    for page in (1, 2):
        api.add_page(reviewer.customer_id, document, page)
        api.add_value(reviewer.customer_id, document, page_no=page, field_name=f"total_{page}")
    _save(reviewer, document, (2, 270))
    items = reviewer.client.get("/api/review").json()["items"]
    assert sorted((item["value"]["page_no"], item["page_turn"]) for item in items) == [(1, 0), (2, 270)]


def _version_turns(db: Database, customer: str) -> dict[str, list[tuple[int, int]]]:
    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT document_id::text, page_no, turn FROM page_rotations WHERE customer_id = :c"),
            {"c": customer},
        ).all()
    turns: dict[str, list[tuple[int, int]]] = {}
    for document, page, turn in sorted(rows):
        turns.setdefault(document, []).append((page, turn))
    return turns


def test_each_version_keeps_its_own_turns_through_a_new_version_make_current_and_separate(
    db: Database,
) -> None:
    api = Api(db)
    a = api.new_signed()
    first = api.add_document(a.customer_id, uploaded_by=a.user_id, page_count=3, is_current=False)
    second = api.add_document(
        a.customer_id, uploaded_by=a.user_id, page_count=3, family_id=first, version_no=2, is_current=False
    )
    _save(a, first, (1, 90))
    third = api.add_document(
        a.customer_id, uploaded_by=a.user_id, page_count=3, family_id=first, version_no=3
    )
    assert _turns(a, third) == []  # a new version starts with none
    _save(a, third, (2, 180))
    expected = {first: [(1, 90)], third: [(2, 180)]}
    assert _version_turns(db, a.customer_id) == expected
    assert a.client.post(f"/api/documents/{first}/make-current", headers=CSRF).status_code == 200
    assert _version_turns(db, a.customer_id) == expected
    reply = a.client.post(f"/api/documents/{third}/separate", json={"name": "apart.pdf"}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert _version_turns(db, a.customer_id) == expected
    assert (_turns(a, first), _turns(a, second)) == ([{"page_no": 1, "turn": 90}], [])


def test_a_file_joined_as_a_new_version_keeps_its_turns(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    old, new = _satbaras(api, db, signed)
    _save(signed, old, (1, 90))
    _save(signed, new, (1, 270))
    _spot(db, signed.customer_id, new)
    _accept(signed, db, new)
    assert _version_turns(db, signed.customer_id) == {old: [(1, 90)], new: [(1, 270)]}


def test_a_random_id_is_404(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    reply = _save(a, str(uuid.uuid4()), (1, 90))
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
