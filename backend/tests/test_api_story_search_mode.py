"""POST /api/me/story-matches, the route of "Search my stories" and of the side cards
(council 3 Oct 2026, "story search mode"): its body and checks, the modes, searched
and all, a question with no content word, its own rate limit, the busy 503, and that
it saves nothing and logs no question.
"""

import logging
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api.routers import stories
from kaagaz.db.engine import Database
from tests.test_api_my_story import put
from tests.test_api_story_matches import QUESTION, STORY
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

PATH = "/api/me/story-matches"


def post(signed: Signed, body: dict[str, Any], *, headers: dict[str, str] = CSRF) -> httpx.Response:
    reply: httpx.Response = signed.client.post(PATH, json=body, headers=headers)
    return reply


def found(signed: Signed, q: str, **body: Any) -> Any:
    reply = post(signed, {"q": q, **body})
    assert reply.status_code == 200, reply.text
    return reply.json()


def _with_stories(db: Database, count: int, story: str = STORY) -> tuple[Api, Signed, list[str]]:
    api = Api(db)
    signed = api.new_signed()
    documents = [api.add_document(signed.customer_id, name=f"doc {i}.pdf") for i in range(count)]
    for document in documents:
        assert put(signed, document, story).status_code == 201
    return api, signed, documents


def test_post_checks_its_body_like_any_write(db: Database) -> None:
    api, signed, (document,) = _with_stories(db, 1)

    assert found(signed, QUESTION) == {
        "story_matches": [
            {"document_id": document, "display_name": "doc 0.pdf", "doc_type": None, "band": "match"}
        ],
        "searched": 1,
        "all": True,
    }
    assert post(signed, {"q": QUESTION}, headers={}).status_code == 403  # no CSRF header
    assert post(signed, {"q": QUESTION, "other": 1}).status_code == 400
    assert post(signed, {"q": "x" * 1001}).status_code == 400
    assert post(signed, {"q": ""}).status_code == 400
    for limit in (0, 11, "x", "3", True, 2.5):
        assert post(signed, {"q": QUESTION, "limit": limit}).status_code == 400, limit
    assert post(signed, {"q": QUESTION, "mode": "answer"}).status_code == 400
    assert api.client().post(PATH, json={"q": QUESTION}, headers=CSRF).status_code == 401
    # The GET form still answers, as before.
    reply = signed.client.get(PATH, params={"q": QUESTION})
    assert [m["document_id"] for m in reply.json()["story_matches"]] == [document]


def test_limit_and_mode(db: Database) -> None:
    _, signed, documents = _with_stories(db, 12, "Muthoot gold loan ki parchi, mummy ke kangan")

    assert len(found(signed, "gold loan")["story_matches"]) == 3  # the default
    assert len(found(signed, "gold loan", limit=1)["story_matches"]) == 1
    reply = found(signed, "gold loan", limit=10, mode="stories")
    assert len(reply["story_matches"]) == 10 and (reply["searched"], reply["all"]) == (12, True)
    assert {m["band"] for m in reply["story_matches"]} == {"maybe"}  # twelve twins: no "match"
    # One near word: nothing for the side cards, "maybe" when searching stories.
    assert found(signed, "kangun")["story_matches"] == []
    assert found(signed, "kangann", mode="cards")["story_matches"] == []
    near = found(signed, "kangann", mode="stories", limit=10)["story_matches"]
    assert len(near) == 10 and {m["band"] for m in near} == {"maybe"}
    assert {m["document_id"] for m in near} < set(documents)


def test_a_question_with_no_content_word_runs_no_sql(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _, signed, _ = _with_stories(db, 1)

    def no_sql(*_: object, **__: object) -> None:
        raise AssertionError("no SQL for a question with no content word")

    monkeypatch.setattr(stories, "resolve", no_sql)
    monkeypatch.setattr(stories, "search_stories", no_sql)
    for q in ("the papers of", "   ", "ka ne 7"):
        assert found(signed, q, mode="stories") == {
            "story_matches": [],
            "searched": 0,
            "all": True,
            "reason": "no_content_words",
        }


def test_its_own_limit_is_20_a_minute_and_search_is_not_held(db: Database) -> None:
    _, signed, _ = _with_stories(db, 1)
    assert stories.STORY_MATCHES_PER_MINUTE == 20
    for _ in range(20):
        assert post(signed, {"q": "the of"}).status_code == 200
    limited = post(signed, {"q": QUESTION})
    assert (limited.status_code, limited.headers["Retry-After"]) == (429, "60")
    assert limited.json()["error"]["code"] == "rate_limited"
    assert signed.client.get(PATH, params={"q": QUESTION}).status_code == 429  # the GET form too
    assert signed.client.get("/api/search", params={"q": QUESTION}).status_code == 200


def test_busy_ranking_is_503_at_once(db: Database) -> None:
    _, signed, _ = _with_stories(db, 1)
    taken = [stories.ranking_slots.acquire(blocking=False) for _ in range(stories.RANKING_AT_ONCE)]
    try:
        assert all(taken)
        busy = post(signed, {"q": QUESTION})
        assert (busy.status_code, busy.json()["error"]["code"]) == (503, "backend_unavailable")
    finally:
        for _ in taken:
            stories.ranking_slots.release()
    assert post(signed, {"q": QUESTION}).status_code == 200  # the slots were given back


def test_it_saves_nothing_and_logs_no_question(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _, signed, _ = _with_stories(db, 1)

    counts = text(
        "SELECT (SELECT count(*) FROM conversations WHERE customer_id = :c),"
        " (SELECT count(*) FROM messages WHERE customer_id = :c),"
        " (SELECT count(*) FROM audit_log WHERE customer_id = :c)"
    )

    def rows() -> tuple[int, ...]:
        """This customer's conversations, messages and audit rows."""
        with db.system() as conn:
            return tuple(conn.execute(counts, {"c": signed.customer_id}).one())

    before = rows()
    caplog.clear()
    for mode in ("cards", "stories", "cards", "stories", "stories"):
        assert found(signed, "Daregaon Zorbanukh jamin", mode=mode)["story_matches"]
    assert rows() == before
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "zorbanukh" not in logged.lower() and "daregaon" not in logged.lower()
