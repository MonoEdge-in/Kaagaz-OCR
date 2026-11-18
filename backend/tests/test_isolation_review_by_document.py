"""PRD 9 F-17: Review's "By document" list (the Review page's own request,
``GET /api/documents?attention=true&limit=100``) never shows another customer's documents, nor a
document of the person's own organisation they can't open (PRD 7 walls). The control: each sees
its own flagged documents, a page kept as is leaves the list, and a waiting value alone is enough."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_page_fixes import accept, read_page
from tests.test_api_support import Api
from tests.test_isolation_access_views import _walled

pytestmark = pytest.mark.db

# The parameters frontend/components/review/review.ts (REVIEW_DOCUMENTS) sends.
REVIEW_DOCUMENTS = {"attention": "true", "limit": 100}


def _listed(reply_json: dict[str, object]) -> list[str]:
    items = reply_json["items"]
    assert isinstance(items, list)
    return [str(item["id"]) for item in items]


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


def test_by_document_never_lists_a_document_the_person_cannot_open(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    # "plain", a reviewer in no department, may open "given" (a Finance file given to them) and
    # "top" (in no folder), never "fin" (Finance is Department only) or "sal" (a private folder).
    for name in ("fin", "sal", "given", "top"):
        read_page(api, org.c, org.files[name], read_quality="unreadable", read_issues=["no_text"])
        api.add_value(org.c, org.files[name])

    plain = org.people["plain"].client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert plain.status_code == 200
    assert sorted(_listed(plain.json())) == sorted([org.files["given"], org.files["top"]])
    assert plain.json()["total"] == 2
    assert "fin.pdf" not in plain.text and "sal.pdf" not in plain.text

    # The control: Finance's head sees the Finance file, so it was listable.
    head = org.people["head"].client.get("/api/documents", params=REVIEW_DOCUMENTS)
    assert org.files["fin"] in _listed(head.json())
