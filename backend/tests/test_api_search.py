"""GET /api/search (PRD 3 Q2) through the whole API, signed in, on PostgreSQL. Pieces lie on
axis 0 with the settings' recipe; ``FakeModel`` embeds the first question as axis 0, or fails."""

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search.indexing import recipe_tag
from tests.test_api_support import Api, Signed
from tests.test_indexing_db import FakeModel
from tests.test_search_db import REGION, add_pieces, axis

pytestmark = pytest.mark.db

PIECE = "Invoice INV-7001 from Patil Traders <img src=x onerror=alert(1)> [pay](https://evil.example/x)"


def _signed(db: Database, model: FakeModel | None = None) -> tuple[Api, Signed]:
    api = Api(db, model=model)
    return api, api.new_signed()


def _document(api: Api, signed: Signed, texts: list[str], **columns: Any) -> str:
    document = api.add_document(signed.customer_id, **columns)
    vectors: list[tuple[str, list[float] | None]] = [(piece, axis(0)) for piece in texts]
    add_pieces(api.db, signed.customer_id, document, vectors, model=recipe_tag(api.settings.model_embed))
    return document


def _search(signed: Signed, **params: Any) -> Any:
    reply = signed.client.get("/api/search", params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_results_come_grouped_by_document_with_a_clean_snippet(db: Database) -> None:
    api, signed = _signed(db)
    document = _document(api, signed, [PIECE, "INV-7001 again on page two"], name="patil-bill.pdf")
    with db.customer(signed.customer_id) as conn:
        sql = "UPDATE documents SET doc_type = 'invoice' WHERE customer_id = :c AND id = :d"
        conn.execute(text(sql), {"c": signed.customer_id, "d": document})

    (found,) = _search(signed, q="INV-7001 Patil")["documents"]

    assert found["score"] > 0
    snippet = "Invoice INV-7001 from Patil Traders pay"  # no tag, no link target
    best = {"snippet": snippet, "page_from": 1, "page_to": 1, "regions": [REGION]}
    expected = {
        "id": document,
        "display_name": "patil-bill.pdf",
        "doc_type": "invoice",
        "best": best,
        "more": 1,
    }
    assert found == {**expected, "score": found["score"]}


def test_the_scope_comes_from_the_query_string(db: Database) -> None:
    """Folder, tag and document ids: test_isolation_search.py, through this route too."""
    api, signed = _signed(db)
    pdf, png = _document(api, signed, [PIECE]), _document(api, signed, [PIECE], detected_type="png")

    def ids(**params: Any) -> set[str]:
        return {found["id"] for found in _search(signed, q="INV-7001", **params)["documents"]}

    assert ids() == {pdf, png}
    assert ids(detected_type="png") == {png}
    assert ids(document_ids=[pdf, str(uuid.uuid4())]) == {pdf}
    assert ids(doc_types=["receipt"]) == ids(folder_id=str(uuid.uuid4())) == set()  # nothing, not 404


def test_with_the_model_off_or_down_words_and_typos_still_answer(db: Database) -> None:
    for model in (None, FakeModel(fail=True)):
        api, signed = _signed(db, model)
        document = _document(api, signed, [PIECE])
        assert [found["id"] for found in _search(signed, q="invioce patil")["documents"]] == [document]


def test_a_ready_document_with_no_vectors_yet_is_found_by_its_words_with_its_spot(db: Database) -> None:
    """Fast scan (council 16 Oct 2026): a document is ready before its vectors are made; with
    the model on, its pieces are still found by their words, each with its page and spot."""
    api, signed = _signed(db, FakeModel())
    document = api.add_document(signed.customer_id)
    add_pieces(
        api.db, signed.customer_id, document, [(PIECE, None)], model=recipe_tag(api.settings.model_embed)
    )
    (found,) = _search(signed, q="INV-7001 Patil")["documents"]
    assert (found["id"], found["best"]["page_from"], found["best"]["regions"]) == (document, 1, [REGION])


def test_with_the_model_on_a_question_with_no_shared_word_finds_nothing(db: Database) -> None:
    """KZ-003: meaning alone always has a nearest piece, so it only re-ranks word or typo matches."""
    model = FakeModel()
    api, signed = _signed(db, model)
    _document(api, signed, [PIECE])

    assert _search(signed, q="xyzzy plugh")["documents"] == []
    assert len(model.calls) == 1
    assert model.calls[0][0].endswith("\nQuery:xyzzy plugh")


def test_a_scope_that_matches_nothing_never_calls_the_model(db: Database) -> None:
    model = FakeModel()
    api, signed = _signed(db, model)
    _document(api, signed, [PIECE])

    found = _search(signed, q="INV-7001", tag_ids=[str(uuid.uuid4())])
    assert found == {"documents": [], "story_matches": []}
    assert model.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {},  # no question
        {"q": "x" * 201},
        {"q": "invoice", "customer_id": str(uuid.uuid4())},  # an unknown key
        {"q": "invoice", "document_ids": [str(uuid.uuid4()) for _ in range(51)]},
        {"q": "invoice", "tag_ids": [str(uuid.uuid4()) for _ in range(51)]},
        {"q": "invoice", "document_ids": ["not-an-id"]},
        {"q": "invoice", "doc_types": ["passport"]},
        {"q": "invoice", "date_from": "2026-02-30"},
        {"q": "invoice", "date_from": "2026-03-02", "date_to": "2026-03-01"},
        {"q": "invoice", "detected_type": "exe"},
    ],
)
def test_a_bad_query_string_is_refused(db: Database, params: dict[str, Any]) -> None:
    _, signed = _signed(db)
    reply = signed.client.get("/api/search", params=params)
    assert reply.status_code == 400, params
    assert reply.json()["error"]["code"] == "bad_request"


def test_signed_out_is_401(db: Database) -> None:
    api = Api(db)
    reply = api.client().get("/api/search", params={"q": "invoice"})
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_signed_in"


def test_the_question_never_reaches_the_logs(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    """Every log line the server writes, at any level (the test client's own httpx lines aside)."""
    api, signed = _signed(db, FakeModel())
    _document(api, signed, ["Invoice Thackeray7731"])
    caplog.set_level(logging.DEBUG)

    assert _search(signed, q="Thackeray7731")["documents"]

    server = [record for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))]
    assert any(record.name == "kaagaz.api.access" for record in server)
    assert all("Thackeray7731" not in record.getMessage() for record in server)
