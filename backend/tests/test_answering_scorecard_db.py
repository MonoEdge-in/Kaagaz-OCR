"""``kaagaz score-ask`` end to end on PostgreSQL (#721): the seed questions
(tests/fixtures/ask_seed.json) over seeded documents, with no model, as one customer."""

import io
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from kaagaz.chunking.script import piece_latin
from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings

pytestmark = pytest.mark.db

SEED = Path(__file__).parent / "fixtures" / "ask_seed.json"
# A price list whose one page says "rate per bag" twice in each of twelve pieces: it outranks the
# one quote that answers, and before #722 filled every evidence slot.
CROWD = [f"Rate per bag: Rs {380 + n}. Rate per bag with GST: Rs {449 + n}." for n in range(12)]
DOCUMENTS = {
    "tax-note.pdf": [
        (1, "Tax note for the site office, March 2026."),
        (2, "GST rate on cement is 28 percent."),
    ],
    "price-list.pdf": [*((1, piece) for piece in CROWD), (2, "Steel: Rs 62 a kg.")],
    "quote-b.pdf": [(1, "Balaji Suppliers: Rs 395 is the rate per bag for March.")],
    "sale-deed.pdf": [
        (1, "Seller: Ramesh Patil of Wagholi."),
        (3, "Buyer: Sunita Shinde of Pune."),
        (4, "Registered at Jalna on 12 March 2026."),
    ],
    "lease.pdf": [(2, "Monthly rent Rs 25,000, due on the 5th of each month.")],
}
_PIECE = text(
    "INSERT INTO pieces (id, customer_id, document_id, seq, page_from, page_to, text, regions, label,"
    " text_latin) VALUES (:id, :c, :d, :seq, :page, :page, :text, CAST(:regions AS jsonb), 'verified',"
    " :latin)"
)


def seed(db: Database, customer_id: str) -> None:
    """The documents, each piece on its page."""
    with db.customer(customer_id) as conn:
        for name, pieces in DOCUMENTS.items():
            document = insert_document(conn, customer_id, display_name=name)
            for seq, (page, piece) in enumerate(pieces):
                region = json.dumps([{"kind": "box", "page": page, "box": [0.1, 0.2, 0.6, 0.25]}])
                row = {
                    "id": str(uuid.uuid4()),
                    "c": customer_id,
                    "d": document,
                }
                conn.execute(
                    _PIECE,
                    {
                        **row,
                        "seq": seq,
                        "page": page,
                        "text": piece,
                        "regions": region,
                        "latin": piece_latin(piece),
                    },
                )


def score(db: Database, customer_id: str) -> list[str]:
    out = io.StringIO()
    argv = ["score-ask", "--customer-id", customer_id, "--questions", str(SEED)]
    assert main(argv, db=db, environ={}, out=out, settings=make_settings(answer_max_evidence=8)) == 0
    return out.getvalue().splitlines()


def test_the_seed_set_scores_end_to_end_with_no_model(db: Database) -> None:
    customer, _ = new_customer(db)
    seed(db, customer)

    lines = score(db, customer)

    # The quote is the 13th hit: found only because one page keeps at most two slots (#722).
    assert lines[0] == "all: recall 4 of 5 (0.80), MRR 0.67"
    assert lines[1:] == [
        "fact: recall 3 of 4 (0.75), MRR 0.75",
        "list: recall 1 of 1 (1.00), MRR 0.33",
        "missed: When was the property bought?",
    ]


@pytest.mark.isolation
def test_another_customers_documents_never_count(db: Database) -> None:
    mine, _ = new_customer(db)
    theirs, _ = new_customer(db)
    seed(db, theirs)

    assert score(db, mine)[0] == "all: recall 0 of 5 (0.00), MRR 0.00"
