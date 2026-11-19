"""Straighten pages (#709): POST /api/documents/{id}/straighten reads each stored page picture and
answers the turn each page needs (kaagaz/reading/orientation.py), saving nothing. Who may ask is
who may see the page image; a locked file is refused in test_isolation_locked_files (REFUSED), a
hidden one answers as a random id in test_isolation_access_views (``_ROUTES``), and another
customer's in test_isolation_api."""

import io
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

from kaagaz.api.routers import documents
from kaagaz.db import trash
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, NOW, Api, Signed

pytestmark = pytest.mark.db
pytest.importorskip("rapidocr", reason='needs the ocr extra: pip install -e ".[dev,ocr]"')


def _picture(turn: int, *, blank: bool = False) -> bytes:
    """A page of printed lines, turned left by ``turn`` (so it needs ``turn`` to the right), WebP."""
    page = Image.new("RGB", (1240, 1754), "white")
    if not blank:
        draw = ImageDraw.Draw(page)
        for row in range(7):
            line = f"Sharma Traders invoice line {row + 1} total Rs {row + 1}20 paid"
            draw.text((90, 140 + row * 80), line, fill="black", font=ImageFont.load_default(size=30))
    out = io.BytesIO()
    page.rotate(turn, expand=True).save(out, format="WEBP")
    return out.getvalue()


def _straighten(signed: Signed, document: str) -> Any:
    return signed.client.post(f"/api/documents/{document}/straighten", headers=CSRF)


def test_each_page_gets_the_turn_it_needs_and_nothing_is_saved(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, page_count=4)
    pictures = {1: _picture(90), 2: _picture(0), 3: _picture(0, blank=True)}  # page 4 has no picture
    for page_no, picture in pictures.items():
        api.storage.page_images[(a.customer_id, document, page_no)] = picture
    reply = _straighten(a, document)
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"pages": [{"page_no": 1, "turn": 90}], "unsure": [3, 4]}
    with db.customer(a.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM page_rotations")).scalar_one() == 0


def test_a_file_in_trash_is_409_and_a_rejected_one_404(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id)
    with db.customer(a.customer_id) as conn:
        assert trash.trash_document(conn, a.customer_id, document, by=a.user_id, now=NOW)
    reply = _straighten(a, document)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "in_trash")
    rejected = api.add_document(a.customer_id, uploaded_by=a.user_id, status="rejected")
    reply = _straighten(a, rejected)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")


def test_a_file_whose_pages_are_not_counted_yet_is_409_not_ready(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, status="clean", page_count=None)
    reply = _straighten(a, document)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "not_ready")


def test_a_file_of_more_pages_than_the_cap_is_refused_in_plain_words_and_nothing_is_read(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    a = api.new_signed()
    pages = documents.STRAIGHTEN_MAX_PAGES + 1
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, page_count=pages)
    read: list[int] = []
    monkeypatch.setattr(api.storage, "get_page_image", lambda c, d, page_no: read.append(page_no))
    reply = _straighten(a, document)
    assert reply.status_code == 409
    assert reply.json()["error"] == {
        "code": "too_many_pages",
        "message": f"Kaagaz can check up to 50 pages at a time. This file has {pages}.",
    }
    assert read == []


def test_a_second_check_while_one_runs_is_429_with_plain_words(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id)
    with documents._straightening:
        reply = _straighten(a, document)
    assert reply.status_code == 429
    assert reply.json()["error"] == {
        "code": "rate_limited",
        "message": "Kaagaz is checking another file's pages. Try again in a moment.",
    }
    assert reply.headers["Retry-After"] == "10"


def test_pages_left_when_time_runs_out_are_unsure(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api(db)
    a = api.new_signed()
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, page_count=2)
    for page_no in (1, 2):
        api.storage.page_images[(a.customer_id, document, page_no)] = _picture(90)
    monkeypatch.setattr(documents, "STRAIGHTEN_SECONDS", 0.0)
    reply = _straighten(a, document)
    assert (reply.status_code, reply.json()) == (200, {"pages": [], "unsure": [1, 2]})
