"""The reads that left no trace (#482, Tushar, 3 Nov 2026), against PostgreSQL.

* Every way of reading a document one by one (the viewer's file fetch, page images, word
  lists, readings, cells, the clear copy, compare) writes ``document.view`` with its ``via``,
  sharing the detail's half hour: a second read, or the detail after it, writes nothing.
* Views of one document at the same moment write one row (the view lock).
* A search writes one ``search.run`` row with counts only, never its words.
* The usage CSV writes one ``usage.export`` row with its period.
"""

import threading
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import audit
from kaagaz.db.engine import Database
from tests.test_api_support import Api, Signed
from tests.test_file_management_activity_db import family
from tests.test_search_db import add_pieces

pytestmark = pytest.mark.db

_ROWS = text(
    "SELECT action, target_id, details FROM audit_log WHERE customer_id = :c AND action = ANY(:a) ORDER BY id"
)


def rows(db: Database, customer: str, *actions: str) -> list[tuple[str, str, dict[str, Any]]]:
    with db.customer(customer) as conn:
        return [
            (str(a), str(t), dict(d)) for a, t, d in conn.execute(_ROWS, {"c": customer, "a": list(actions)})
        ]


def _document(api: Api, signed: Signed) -> str:
    """A ready PDF with one page, its image and a piece of text; a sheet page 2 with a cell row."""
    c = signed.customer_id
    document = api.add_document(c, uploaded_by=signed.user_id)
    api.add_page(c, document, 1)
    api.add_page(c, document, 2, unit="cells", height=1)
    api.storage.page_images[(c, document, 1)] = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"
    add_pieces(api.db, c, document, [("Total due 4500", None)])
    return document


READS = {
    "file": "/api/documents/{d}/file",
    "page_image": "/api/documents/{d}/pages/1/image",
    "words": "/api/documents/{d}/words?w=total",
    "readings": "/api/documents/{d}/pages/1/readings",
    "cells": "/api/documents/{d}/pages/2/cells",
    "clear_copy": "/api/documents/{d}/clear-copy",
}


@pytest.mark.parametrize("via", sorted(READS))
def test_each_read_one_by_one_is_a_view_once_per_half_hour(db: Database, via: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = _document(api, signed)
    for _ in range(3):
        reply = signed.client.get(READS[via].format(d=document))
        assert reply.status_code == 200, reply.text
    assert signed.client.get(f"/api/documents/{document}").status_code == 200  # the detail: viewed already
    assert rows(db, signed.customer_id, "document.view") == [("document.view", document, {"via": via})]


def test_the_download_button_is_a_download_not_a_view(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = _document(api, signed)
    assert signed.client.get(f"/api/documents/{document}/file", params={"download": "1"}).status_code == 200
    assert rows(db, signed.customer_id, "document.view", "document.download") == [
        ("document.download", document, {})
    ]


def test_compare_is_a_view_of_both_versions(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, second = family(api, signed)
    reply = signed.client.get(f"/api/documents/{second}/compare", params={"with": first})
    assert reply.status_code == 200, reply.text
    assert sorted(rows(db, signed.customer_id, "document.view")) == sorted(
        ("document.view", each, {"via": "compare"}) for each in (first, second)
    )


def test_views_at_the_same_moment_write_one_row(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = _document(api, signed)
    start = threading.Barrier(8)

    def view() -> None:
        start.wait()
        with db.customer(signed.customer_id) as conn:
            audit.record_view(
                conn, signed.customer_id, actor_user_id=signed.user_id, document_id=document, via="page_image"
            )

    threads = [threading.Thread(target=view) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(rows(db, signed.customer_id, "document.view")) == 1


def test_a_search_is_one_row_with_counts_and_never_its_words(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _document(api, signed)
    for _ in range(2):
        reply = signed.client.get("/api/search", params={"q": "Total due"})
        assert reply.status_code == 200, reply.text
        assert len(reply.json()["documents"]) == 1
    logged = rows(db, signed.customer_id, "search.run")
    assert logged == [("search.run", signed.customer_id, {"documents": 1, "capped": False})] * 2
    assert "total" not in str(logged).lower()


def test_the_usage_export_is_logged_with_its_period(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    reply = admin.client.get("/api/admin/usage.csv", params={"from": "2026-10-01", "to": "2026-10-31"})
    assert reply.status_code == 200, reply.text
    assert rows(db, admin.customer_id, "usage.export") == [
        ("usage.export", admin.customer_id, {"from": "2026-10-01", "to": "2026-10-31", "user_id": None})
    ]
