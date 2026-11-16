"""PRD 6 package K (#644): the focus profile and the related-documents statement read one customer's
documents only, under row level security and the customer filter, through LIVE_CURRENT. Another
customer's document with the very same land, owner and village is never a focus nor related."""

from typing import Any

import pytest

from kaagaz.answering.focus import PgFocus
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING
from tests.db_support import new_customer
from tests.land_support import add_land

pytestmark = pytest.mark.db


def test_another_customers_document_is_never_a_focus_nor_related(db: Database) -> None:
    mine, _ = new_customer(db)
    theirs, _ = new_customer(db)
    same: dict[str, Any] = {"places": [("village", "Wagholi")], "survey": "61", "doc_type": "land_record"}
    focus_id = add_land(db, mine, "7-12 Wagholi.pdf", [("Suresh Jadhav", None)], **same)
    related_id = add_land(db, mine, "Sale Deed Wagholi.pdf", buyers=["Suresh Jadhav"], **same)
    their_twin = add_land(db, theirs, "7-12 Wagholi.pdf", [("Suresh Jadhav", None)], **same)
    their_deed = add_land(db, theirs, "Sale Deed Wagholi.pdf", buyers=["Suresh Jadhav"], **same)
    focus = PgFocus(db)

    assert focus.profile(mine, their_twin) is None  # their id, asked as mine: nothing
    profile = focus.profile(mine, focus_id)
    assert profile is not None and {f.value.document_id for f in profile.facts} == {focus_id}

    found = {f.value.document_id for f in focus.matches(mine, profile, EVERYTHING)}
    assert found == {related_id}
    assert their_deed not in found and their_twin not in found
