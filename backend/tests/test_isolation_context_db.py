"""Customer B never gets customer A's whole documents, topic or section pieces, or cards
(PRD 5 CX5, CX6, M2; real PostgreSQL), through the answer service's own port
(``PgRetriever``), even with A's exact document ids in the scope and A's piece as a hit.
Each is paired with A's own call as the positive control. Marked ``isolation`` by its file
name.
"""

import json

import pytest
from sqlalchemy import text

from kaagaz.answering.evidence import PgRetriever
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import new_customer
from tests.test_context_db import OFFER, _offer, _section, _topic
from tests.test_search_db import RECIPE, axis

pytestmark = pytest.mark.db


def _a_with_an_offer(db: Database) -> tuple[str, str, str]:
    """(A, B, A's offer letter with a leave topic, a section with a vector, and a card)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    document, _ = _offer(db, a)
    _topic(db, a, document, "leave", ["holidays"], [1, 3, 4])
    _section(db, a, document, 0, (0, 4), "Leave and holidays.", axis(0))
    with db.customer(a) as conn:
        conn.execute(
            text("UPDATE documents SET card = CAST(:card AS jsonb) WHERE customer_id = :c AND id = :d"),
            {"c": a, "d": document, "card": json.dumps({"title": "Offer"})},
        )
    return a, b, document


def test_another_customer_gets_none_of_the_context(db: Database) -> None:
    a, b, document = _a_with_an_offer(db)
    port = PgRetriever(db, recipe=RECIPE, ef_search=40, neighbours=1, topic_pieces=12)
    theirs = Scope(document_ids=(document,))
    mine = port.whole(a, EVERYTHING, 100_000)
    assert mine is not None and [hit.text for hit in mine] == OFFER  # the control
    a_hit = mine[1]

    for customer, scope in ((b, EVERYTHING), (b, theirs)):
        assert port.whole(customer, scope, 100_000) is None
        assert port.topic_pieces(customer, ["leave", "holidays"], [a_hit], scope) == []
        assert port.section_pieces(customer, ["leave"], axis(0), [a_hit], scope) == []
        assert port.cards(customer, [document], scope) == {}

    assert {hit.seq for hit in port.topic_pieces(a, ["leave"], [], theirs)} == {1, 3, 4}
    assert [hit.seq for hit in port.section_pieces(a, ["leave"], axis(0), [], theirs)] == [0, 1, 2, 3, 4]
    assert port.cards(a, [document], theirs) == {document: ("offer.pdf", {"title": "Offer"})}
