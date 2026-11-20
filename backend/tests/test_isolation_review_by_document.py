"""PRD 9 F-17: Review's "By document" list (the Review page's own request,
``GET /api/documents?attention=true&limit=50``) never shows another customer's documents, nor a
document of the person's own organisation they can't open (PRD 7 walls), with the database's
per-person rule on and off (off, as on release day, the list's own view is what keeps them out).
The control: each sees its own flagged documents, a page kept as is leaves the list, and a
waiting value alone is enough."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import word_review
from kaagaz.db.engine import Database
from kaagaz.extraction.flags import LINE, READINGS_DIFFER, word_key
from tests.db_support import rule_off
from tests.test_api_page_fixes import accept, read_page
from tests.test_api_support import Api, Signed
from tests.test_isolation_access_views import _walled

pytestmark = pytest.mark.db

# The parameters frontend/components/review/review.ts (REVIEW_DOCUMENTS) sends.
REVIEW_DOCUMENTS: dict[str, str | int] = {"attention": "true", "limit": 50}


def _listed(reply_json: Any) -> list[str]:
    return [str(item["id"]) for item in reply_json["items"]]


def test_by_document_never_lists_another_customers_documents(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    flagged = api.add_document(a.customer_id, name="challan.pdf")
    read_page(
        api, a.customer_id, flagged, read_quality="uncertain", read_issues=["unpinned"], unpinned_lines=31
    )
    values_only = api.add_document(a.customer_id, name="bill.pdf")
    read_page(api, a.customer_id, values_only)
    api.add_value(a.customer_id, values_only)
    settled = api.add_document(a.customer_id, name="settled.pdf")
    read_page(api, a.customer_id, settled, read_quality="uncertain", read_issues=["faint"])
    theirs = api.add_document(b.customer_id, name="mehta-secret.pdf")
    read_page(api, b.customer_id, theirs, read_quality="unreadable", read_issues=["no_text"])
    api.add_value(b.customer_id, theirs)

    assert accept(a, settled, 1).status_code == 200
    mine = a.client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert mine.status_code == 200
    # Newest first; the page kept as is has left the list.
    assert _listed(mine.json()) == [values_only, flagged]
    assert mine.json()["total"] == 2
    assert "mehta" not in mine.text
    by_id = {item["id"]: item for item in mine.json()["items"]}
    assert (by_id[flagged]["pages_uncertain"], by_id[flagged]["values_to_review"]) == (1, 0)
    assert (by_id[values_only]["pages_uncertain"], by_id[values_only]["values_to_review"]) == (0, 1)

    own = b.client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert _listed(own.json()) == [theirs]
    assert "challan" not in own.text


@pytest.mark.parametrize("database_rule", ["on", "off"])
def test_by_document_never_lists_a_document_the_person_cannot_open(db: Database, database_rule: str) -> None:
    api = Api(db)
    org = _walled(api)
    # "plain", a reviewer in no department, may open "given" (a Finance file given to them) and
    # "top" (in no folder), never "fin" (Finance is Department only) or "sal" (a private folder).
    for name in ("fin", "sal", "given", "top"):
        read_page(api, org.c, org.files[name], read_quality="unreadable", read_issues=["no_text"])
        api.add_value(org.c, org.files[name])

    with rule_off() if database_rule == "off" else nullcontext():
        plain = org.people["plain"].client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert plain.status_code == 200
    assert sorted(_listed(plain.json())) == sorted([org.files["given"], org.files["top"]])
    assert plain.json()["total"] == 2
    assert "fin.pdf" not in plain.text and "sal.pdf" not in plain.text

    # The control: Finance's head sees the Finance file, so it was listable.
    head = org.people["head"].client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert org.files["fin"] in _listed(head.json())


_TEXT = "Total Rs 4,850 paid"
_REGION: dict[str, Any] = {"kind": "box", "page": 1, "box": [0.1, 0.5, 0.6, 0.55]}
_LINE = {"origin": "ocr", "kind": "paragraph", "text": _TEXT, "region": _REGION, "confidence": 0.97}


def _read_differently(api: Api, signed: Signed, name: str) -> str:
    """A ready document whose page 1 is good, with one line an Ensemble run read differently."""
    document = api.add_document(signed.customer_id, name=name)
    read_page(api, signed.customer_id, document)
    api.add_reader_output(signed.customer_id, document, [_LINE], reader="gemini")
    with api.db.customer(signed.customer_id) as conn:
        run = conn.execute(
            text(
                "INSERT INTO ensemble_runs (customer_id, document_id, scope, pages, models, status,"
                " requested_by, finished_at) VALUES (:c, :d, 'document', '{1}', '{m}', 'done', :u, now())"
                " RETURNING id"
            ),
            {"c": signed.customer_id, "d": document, "u": signed.user_id},
        ).scalar_one()
        key = word_key(LINE, 1, _REGION, _TEXT)  # as ensemble.targets keys a line
        conn.execute(
            text(
                "INSERT INTO ensemble_readings (customer_id, document_id, run_id, word_key, page_no, region,"
                " before_text, outcome, proposal) VALUES (:c, :d, :r, :k, 1, CAST(:region AS jsonb), :t,"
                " 'changed', 'Total Rs 4,350 paid')"
            ),
            {
                "c": signed.customer_id,
                "d": document,
                "r": run,
                "k": key,
                "region": json.dumps(_REGION),
                "t": _TEXT,
            },
        )
    return document


def test_by_document_lists_a_line_ensemble_read_differently_until_it_is_settled(db: Database) -> None:
    # PRD 9 F-17 follow-up: an open READINGS_DIFFER line on a page graded good counts in Needs
    # attention, so its document is listed; settled (a word change in force), it leaves; undone, it
    # comes back; read again without that line, it leaves. Another customer's never shows.
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    mine = _read_differently(api, a, "challan.pdf")
    theirs = _read_differently(api, b, "mehta-secret.pdf")
    with db.customer(a.customer_id) as conn:
        flags = word_review.load(conn, a.customer_id, mine).flags
    assert [(f.reason, f.band) for f in flags] == [(READINGS_DIFFER, "review")]

    listed = a.client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert _listed(listed.json()) == [mine]
    assert "mehta" not in listed.text
    assert _listed(b.client.get("/api/documents", params=REVIEW_DOCUMENTS).json()) == [theirs]

    now = datetime.now(UTC)
    with db.customer(a.customer_id) as conn:
        change = word_review.change_word(
            conn, a.customer_id, mine, flags[0].key, expected_text=_TEXT, new_text=None,
            user_id=a.user_id, now=now,
        )  # fmt: skip
    assert _listed(a.client.get("/api/documents", params=REVIEW_DOCUMENTS).json()) == []
    assert _listed(b.client.get("/api/documents", params=REVIEW_DOCUMENTS).json()) == [theirs]

    with db.customer(a.customer_id) as conn:
        word_review.undo_change(conn, a.customer_id, mine, change, user_id=a.user_id, now=now)
    assert _listed(a.client.get("/api/documents", params=REVIEW_DOCUMENTS).json()) == [mine]

    with db.customer(a.customer_id) as conn:  # read again: the line now reads otherwise
        conn.execute(
            text(
                "UPDATE reader_outputs SET output = jsonb_set(output, '{0,text}', '\"Total Rs 4,350 paid\"')"
                " WHERE customer_id = :c AND document_id = :d"
            ),
            {"c": a.customer_id, "d": mine},
        )
    assert _listed(a.client.get("/api/documents", params=REVIEW_DOCUMENTS).json()) == []
