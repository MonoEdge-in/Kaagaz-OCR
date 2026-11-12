"""Migration 0077c (#524, Tushar, 6 Nov 2026): the amount safety layer's four label reasons.
Up from 0077b adds them to the label_reason CHECK; the downgrade refuses while a value carries
one (one transaction, nothing changed), and once none does it restores 0077b's schema exactly;
up again gives 0077c's. That the CHECK is EVERY_REASON: test_review_queue_db."""

import subprocess

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0004_db import _BOX, _INSERT_VALUE, _current

pytestmark = pytest.mark.db
fresh = m0002.fresh  # the fixture, by its name
AMOUNT_REASONS = ("mixed_digits", "amount_commas", "amount_mark", "amount_outlier")


def test_0077c_adds_the_amount_reasons_and_its_downgrade_refuses_while_one_is_used(
    fresh: tuple[str, Database],
) -> None:
    url, database = fresh
    migrate(url, "0077b")
    at_0077b = m0002._schema(database)
    migrate(url, "0077c")
    at_0077c = m0002._schema(database)
    customer, _ = new_customer(database)
    with database.customer(customer) as conn:
        doc = insert_document(conn, customer)
        for reason in AMOUNT_REASONS:
            conn.execute(_INSERT_VALUE, {"c": customer, "d": doc, "r": _BOX, "reason": reason})

    with pytest.raises(subprocess.CalledProcessError) as refused:
        m0002._downgrade(url, "0077b")
    assert b"values labelled with an amount reason exist" in refused.value.stderr
    # Changed on purpose (#522): 0078 and 0080 follow 0077c now, so it is named, not "head".
    assert _current(url) == "0077c"  # the whole downgrade is one transaction
    with database.customer(customer) as conn:
        conn.execute(text("DELETE FROM extracted_values WHERE customer_id = :c"), {"c": customer})
    m0002._downgrade(url, "0077b")
    assert m0002._schema(database) == at_0077b
    migrate(url, "0077c")
    assert m0002._schema(database) == at_0077c
