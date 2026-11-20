"""``kaagaz score-ask`` end to end on PostgreSQL (#721): the seed questions
(tests/fixtures/ask_seed.json) over seeded documents, as one customer, and its model calls."""

import io
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.chunking.script import piece_latin
from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_golden import FakeModel
from tests.test_search_db import axis

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


def score(db: Database, customer_id: str, *flags: str, model: FakeModel | None = None) -> list[str]:
    out = io.StringIO()
    argv = ["score-ask", "--customer-id", customer_id, "--questions", str(SEED), *flags]
    settings = make_settings(answer_max_evidence=8)
    assert main(argv, db=db, environ={}, out=out, settings=settings, model=model) == 0
    return out.getvalue().splitlines()


class Embeds(FakeModel):
    """Embeds each text as the search's own size of vector (``FakeModel``'s is three numbers)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        super().embed(texts)
        return [axis(0) for _ in texts]


SEED_SCORES = [
    "all: page recall 0.80, any page found 4 of 5, MRR 0.67",
    "list: page recall 1.00, any page found 1 of 1, MRR 0.33",
    "unknown: page recall 0.75, any page found 3 of 4, MRR 0.75",
    "missed 1 of 1 pages: When was the property bought?",
]


def test_the_seed_set_scores_end_to_end_with_no_model_call(db: Database) -> None:
    customer, _ = new_customer(db)
    seed(db, customer)
    model = Embeds()

    lines = score(db, customer, model=model)

    # The quote is the 13th hit: kept (3rd) only because one page keeps at most two slots (#722).
    assert lines == SEED_SCORES
    assert (model.embedded, model.prompts) == ([], [])  # no flag: no model call at all


def test_embed_adds_one_embedding_call_a_question_and_nothing_else(db: Database) -> None:
    customer, _ = new_customer(db)
    seed(db, customer)
    model = Embeds()

    lines = score(db, customer, "--embed", model=model)

    assert lines == SEED_SCORES  # the seed's pieces have no vectors: the meaning list adds nothing
    assert len(model.embedded) == 5 and model.prompts == []


def test_a_failed_embedding_is_said_never_scored_as_if_searched_by_meaning(db: Database) -> None:
    customer, _ = new_customer(db)
    seed(db, customer)

    lines = score(db, customer, "--embed", model=FakeModel(embed_error=ModelUnavailable("timeout")))

    assert lines[-1] == "searched by words only, the embedding failed: 5 of 5 questions"


@pytest.mark.isolation
def test_another_customers_documents_never_count(db: Database) -> None:
    mine, _ = new_customer(db)
    theirs, _ = new_customer(db)
    seed(db, theirs)

    assert score(db, mine)[0] == "all: page recall 0.00, any page found 0 of 5, MRR 0.00"
