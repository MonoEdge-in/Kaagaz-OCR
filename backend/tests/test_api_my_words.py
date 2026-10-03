"""The my words routes (kaagaz/api/routers/words.py) against a real database.

The SQL rules themselves are tested on the helpers (test_user_words_db.py); these
tests check every flow through the API: the replies and their codes, the
second-meaning question and its details, the limits, the text and region checks,
the session check, the spots, the audit rows (ids and counts only) and the logs.
"""

import logging
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"
BOX = {"kind": "box", "page": 2, "box": [0.1, 0.2, 0.3, 0.25]}
_FILL = text(
    "INSERT INTO user_words (customer_id, user_id, word, word_fold, meaning, meaning_fold, source)"
    " SELECT :c, :u, 'w' || n, 'w' || n, 'm', 'm', 'manual' FROM generate_series(1, 500) AS n"
)


def post(signed: Signed, word: str, meaning: str, **body: Any) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        "/api/me/words", json={"word": word, "meaning": meaning, **body}, headers=CSRF
    )
    return reply


def listed(signed: Signed) -> list[tuple[str, str]]:
    reply = signed.client.get("/api/me/words")
    assert reply.status_code == 200
    return [(item["word"], item["meaning"]) for item in reply.json()["items"]]


def audited(api: Api, signed: Signed) -> list[tuple[str, str | None, str | None, dict[str, Any]]]:
    return [row for row in api.audit_rows(signed.customer_id) if row[0].startswith("user_word.")]


def error(reply: httpx.Response) -> tuple[int, str]:
    return reply.status_code, reply.json()["error"]["code"]


def test_a_word_is_created_listed_and_audited_with_ids_and_counts_only(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    assert listed(signed) == []

    reply = post(signed, " Malak ", "owner")

    assert reply.status_code == 201
    body = reply.json()
    assert (body["outcome"], body["warning"]) == ("created", None)
    assert set(body["word"]) == {
        "id",
        "word",
        "meaning",
        "as_read",
        "source",
        "built_in",
        "created_at",
        "updated_at",
    }
    assert (body["word"]["word"], body["word"]["source"]) == ("Malak", "manual")
    assert listed(signed) == [("Malak", "owner")]
    assert audited(api, signed) == [
        (
            "user_word.added",
            "user_word",
            body["word"]["id"],
            {"outcome": "created", "documents": 0, "spot": False},
        )
    ]


def test_the_same_word_twice_is_200_and_another_meaning_asks_before_adding(db: Database) -> None:
    signed = Api(db).new_signed()
    boss = post(signed, "malak", "boss").json()["word"]

    again = post(signed, "MALAK", "Boss")
    asked = post(signed, "malak", "owner")

    assert (again.status_code, again.json()["outcome"], again.json()["word"]["id"]) == (
        200,
        "already",
        boss["id"],
    )
    assert error(asked) == (409, "word_has_meaning")
    assert asked.json()["error"]["details"] == {"existing": [{"id": boss["id"], "meaning": "boss"}]}
    assert listed(signed) == [("malak", "boss")]  # nothing written
    assert post(signed, "malak", "owner", confirm_another=True).status_code == 201
    assert listed(signed) == [("malak", "boss"), ("malak", "owner")]


def test_replace_changes_one_meaning_of_the_same_word_only(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    boss = post(signed, "malak", "boss").json()["word"]

    replaced = post(signed, "malak", "owner", replace_id=boss["id"])

    assert (replaced.status_code, replaced.json()["outcome"], replaced.json()["word"]["id"]) == (
        200,
        "replaced",
        boss["id"],
    )
    assert error(post(signed, "bojha", "charge", replace_id=boss["id"])) == (400, "not_same_word")
    assert error(post(signed, "malak", "charge", replace_id=UNKNOWN)) == (404, "not_found")
    assert listed(signed) == [("malak", "owner")]
    assert [row[0] for row in audited(api, signed)] == ["user_word.added", "user_word.changed"]


def test_the_fourth_meaning_and_the_501st_word_are_409(db: Database) -> None:
    signed = Api(db).new_signed()
    for meaning in ("boss", "owner", "holder"):
        assert post(signed, "malak", meaning, confirm_another=True).status_code == 201
    assert error(post(signed, "malak", "master", confirm_another=True)) == (409, "too_many_meanings")
    assert error(post(signed, "malak", "master")) == (409, "too_many_meanings")  # before the question

    other = Api(db).new_signed()
    with db.user(other.customer_id, other.user_id) as conn:
        conn.execute(_FILL, {"c": other.customer_id, "u": other.user_id})
    assert error(post(other, "malak", "owner")) == (409, "too_many_words")


@pytest.mark.parametrize(
    ("word", "meaning", "message"),
    [
        ("what", "owner", "Chat already reads this word its own way, so it can't be one of your words."),
        ("x" * 41, "owner", "The word can be at most 40 characters."),
        ("malak", "<b>owner</b>", "Use only letters, digits, spaces, commas and hyphens in the meaning."),
        ("33", "owner", "The word needs at least one letter."),
        ("  ", "owner", "Type the word."),
    ],
)
def test_text_that_is_refused_is_400_saying_why(db: Database, word: str, meaning: str, message: str) -> None:
    signed = Api(db).new_signed()
    reply = post(signed, word, meaning)
    assert reply.json() == {"error": {"code": "bad_request", "message": message}}
    assert reply.status_code == 400
    assert listed(signed) == []


def test_a_word_chat_already_knows_is_kept_with_a_warning(db: Database) -> None:
    signed = Api(db).new_signed()
    assert post(signed, "kam", "work").json()["warning"] == "built_in"
    assert post(signed, "kaam", "work").json()["warning"] is None
    items = signed.client.get("/api/me/words").json()["items"]
    assert [(item["word"], item["built_in"]) for item in items] == [
        ("kaam", False),
        ("kam", True),
    ]  # the badge


def test_patch_onto_a_word_with_meanings_asks_first_like_post(db: Database) -> None:
    signed = Api(db).new_signed()
    boss = post(signed, "malak", "boss").json()["word"]
    path = f"/api/me/words/{post(signed, 'bojha', 'charge').json()['word']['id']}"

    asked = signed.client.patch(path, json={"word": "Malak"}, headers=CSRF)
    confirmed = signed.client.patch(path, json={"word": "Malak", "confirm_another": True}, headers=CSRF)

    assert error(asked) == (409, "word_has_meaning")
    assert asked.json()["error"]["details"] == {"existing": [{"id": boss["id"], "meaning": "boss"}]}
    assert confirmed.status_code == 200
    assert listed(signed) == [("malak", "boss"), ("Malak", "charge")]


def test_patch_with_replace_id_takes_the_place_of_that_meaning(db: Database) -> None:
    signed = Api(db).new_signed()
    boss = post(signed, "malak", "boss").json()["word"]
    holder = post(signed, "bojha", "holder").json()["word"]
    charge = post(signed, "bojha", "charge", confirm_another=True).json()["word"]

    def patch(word_id: str, **body: Any) -> httpx.Response:
        reply: httpx.Response = signed.client.patch(f"/api/me/words/{word_id}", json=body, headers=CSRF)
        return reply

    assert error(patch(charge["id"], word="malak", replace_id=charge["id"])) == (409, "conflict")  # itself
    assert error(patch(charge["id"], word="malak", replace_id=holder["id"])) == (400, "not_same_word")
    assert error(patch(charge["id"], word="malak", replace_id=UNKNOWN)) == (404, "not_found")
    replaced = patch(charge["id"], word="malak", replace_id=boss["id"])

    assert (replaced.status_code, replaced.json()["word"]["id"]) == (200, charge["id"])  # keeps its id
    assert listed(signed) == [("bojha", "holder"), ("malak", "charge")]


def test_patch_changes_the_word_or_meaning_and_delete_removes_it(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    word_id = post(signed, "malak", "boss").json()["word"]["id"]
    path = f"/api/me/words/{word_id}"

    changed = signed.client.patch(path, json={"meaning": "owner"}, headers=CSRF)
    renamed = signed.client.patch(path, json={"word": "kam"}, headers=CSRF)
    empty = signed.client.patch(path, json={}, headers=CSRF)

    assert (changed.status_code, changed.json()["word"]["meaning"], changed.json()["warning"]) == (
        200,
        "owner",
        None,
    )
    assert (renamed.json()["word"]["word"], renamed.json()["warning"]) == ("kam", "built_in")
    assert error(empty) == (400, "bad_request")
    assert signed.client.delete(path, headers=CSRF).status_code == 204
    assert error(signed.client.delete(path, headers=CSRF)) == (404, "not_found")
    assert error(signed.client.patch(path, json={"meaning": "x"}, headers=CSRF)) == (404, "not_found")
    assert listed(signed) == []
    assert audited(api, signed)[1:] == [
        ("user_word.changed", "user_word", word_id, {"word": False, "meaning": True}),
        ("user_word.changed", "user_word", word_id, {"word": True, "meaning": False}),
        ("user_word.deleted", "user_word", word_id, {}),
    ]


def test_every_write_from_a_tab_of_another_account_is_session_changed(db: Database) -> None:
    signed = Api(db).new_signed()
    word_id = post(signed, "malak", "boss").json()["word"]["id"]
    path = f"/api/me/words/{word_id}"
    for reply in (
        post(signed, "bojha", "charge", user_id=UNKNOWN),
        signed.client.patch(path, json={"meaning": "owner", "user_id": UNKNOWN}, headers=CSRF),
        signed.client.delete(f"{path}?user_id={UNKNOWN}", headers=CSRF),
    ):
        assert error(reply) == (409, "session_changed")
        assert reply.json()["error"]["message"].startswith("Not saved.")
    assert post(signed, "bojha", "charge", user_id=signed.user_id).status_code == 201  # the same user
    assert post(signed, "kaam", "work", user_id=signed.user_id.upper()).status_code == 201  # in capitals too
    assert listed(signed) == [("bojha", "charge"), ("kaam", "work"), ("malak", "boss")]


def test_words_from_an_upload_and_a_viewer_spot_are_linked_to_their_documents(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, second = (api.add_document(signed.customer_id, page_count=3) for _ in range(2))
    spot = {"document_id": first, "page_no": 2, "region": BOX}

    uploaded = post(signed, "bojha", "charge", document_ids=[first, second, first])
    viewed = post(signed, "malak", "owner", spot=spot)
    on_page = signed.client.get(f"/api/documents/{first}/my-words?page=2")

    assert (uploaded.status_code, uploaded.json()["word"]["source"]) == (201, "upload")
    assert (viewed.status_code, viewed.json()["word"]["source"]) == (201, "viewer")
    assert [(i["word"], i["meaning"], i["page_no"], i["region"]) for i in on_page.json()["items"]] == [
        ("malak", "owner", 2, BOX)
    ]
    assert set(on_page.json()["items"][0]) == {"id", "word_id", "word", "meaning", "page_no", "region"}
    assert signed.client.get(f"/api/documents/{first}/my-words?page=1").json() == {"items": []}
    assert error(signed.client.get(f"/api/documents/{UNKNOWN}/my-words?page=1")) == (404, "not_found")
    assert audited(api, signed)[0][3] == {"outcome": "created", "documents": 2, "spot": False}
    with db.user(signed.customer_id, signed.user_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM user_word_spots")).scalar_one() == 3


def _box(**changes: Any) -> dict[str, Any]:
    return {**BOX, **changes}


@pytest.mark.parametrize(
    ("spot", "status"),
    [
        ({"page_no": 3, "region": BOX}, 400),  # the region is on another page
        ({"page_no": 4, "region": _box(page=4)}, 400),  # past the document's 3 pages
        ({"region": _box(box=[0.3, 0.2, 0.1, 0.25])}, 400),  # corners out of order
        ({"region": _box(box=[0.1, 0.2, 1.5, 0.25])}, 400),  # past the page
        ({"region": _box(box=[0.1, 0.2, 0.3])}, 400),  # three numbers
        ({"region": _box(box=["0.1", 0.2, 0.3, 0.25])}, 400),  # a string
        ({"region": _box(page="2")}, 400),
        ({"region": _box(note="x")}, 400),  # an unknown key
        ({"region": {"kind": "cell", "page": 2, "row": 0, "column": 1}}, 400),
        ({"region": {"kind": "cell", "page": 2, "row": 10**30, "column": 1}}, 400),  # never a 500
        ({"region": {"kind": "line", "page": 2}}, 400),
        ({"extra": True}, 400),
        ({"document_id": UNKNOWN}, 404),
    ],
)
def test_a_bad_spot_is_refused_and_nothing_is_written(
    db: Database, spot: dict[str, Any], status: int
) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id, page_count=3)
    reply = post(
        signed, "malak", "owner", spot={"document_id": document, "page_no": 2, "region": BOX, **spot}
    )
    assert reply.status_code == status
    assert listed(signed) == []


def test_too_many_documents_or_an_unknown_one_writes_nothing(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    assert error(post(signed, "malak", "owner", document_ids=[document] * 21)) == (400, "bad_request")
    assert error(post(signed, "malak", "owner", document_ids=[document, UNKNOWN])) == (404, "not_found")
    assert listed(signed) == []


def test_no_word_or_meaning_reaches_the_logs(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="sqlalchemy")  # statements, never result rows
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id, page_count=3)
    word_id = post(
        signed, "Zqmalakword", "Zqownermeaning", spot={"document_id": document, "page_no": 2, "region": BOX}
    )
    post(signed, "Zqmalakword", "Zqsecondmeaning")  # the 409 question
    signed.client.patch(
        f"/api/me/words/{word_id.json()['word']['id']}", json={"meaning": "Zqthird"}, headers=CSRF
    )
    signed.client.get("/api/me/words")
    signed.client.get(f"/api/documents/{document}/my-words?page=2")
    assert "request" in caplog.text  # the access log did log
    assert [
        marker for marker in ("zqmalak", "zqowner", "zqsecond", "zqthird") if marker in caplog.text.lower()
    ] == []
