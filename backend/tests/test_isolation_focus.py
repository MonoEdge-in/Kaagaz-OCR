"""PRD 6 package K (#644): the focus profile and the related-documents statement read one customer's
documents only, under row level security and the customer filter, through LIVE_CURRENT, and of them
only those the asker may see (``visible_documents``, PRD 7, #610). Another customer's document with
the very same land, owner and village, or a document hidden from the asker, is never a focus nor
related."""

import uuid
from typing import Any

import pytest

from kaagaz.answering.focus import PgFocus
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING
from tests.db_support import insert_folder, new_customer
from tests.land_support import add_land
from tests.test_ask_focus_db import World, never_named
from tests.test_operator_auth_db import _owner

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

    assert focus.profile(mine, None, their_twin) is None  # their id, asked as mine: nothing
    profile = focus.profile(mine, None, focus_id)
    assert profile is not None and {f.value.document_id for f in profile.facts} == {focus_id}

    found = {f.value.document_id for f in focus.matches(mine, None, profile, EVERYTHING)}
    assert found == {related_id}
    assert their_deed not in found and their_twin not in found


def test_a_follow_up_about_a_focus_now_hidden_answers_as_about_an_id_that_never_was(db: Database) -> None:
    """PRD 7 (#610): the asker saw the deed and asked about it; it then moved into a private folder
    they are not a member of. Their follow-up answers exactly as one after a question asked in a
    document that never was: no understood line, no name, no fact of it. As that person, the focus
    statement finds no profile and the related statement never lists it."""
    world = World(db)
    with db.customer(world.customer) as conn:
        walled = insert_folder(conn, world.customer, world.admin, "Walled")
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": walled})
    seen = world.first(world.deed)
    never = world.ask("What is this document about?", scope={"document_ids": [str(uuid.uuid4())]})
    _owner(db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": walled, "d": world.deed})

    question = "Is this measurement in any other document?"
    replies = [
        world.ask(question, conversation_id=first["conversation_id"], scope={})["answer"]["answer"]
        for first in (seen, never)
    ]

    def shown(answer: dict[str, Any]) -> tuple[Any, ...]:
        cited = sorted({c["document_id"] for c in answer["citations"]})
        texts = [s["text"] for s in answer["sentences"]]
        return answer["status"], texts, cited, answer["assumptions"], answer["rewritten_question"]

    assert shown(replies[0]) == shown(replies[1])
    assert not replies[0]["assumptions"]
    never_named(replies[0], world, world.deed)
    focus, user = PgFocus(db), world.signed.user_id
    assert focus.profile(world.customer, user, world.deed) is None
    extract = focus.profile(world.customer, user, world.extract)
    assert extract is not None
    as_person = {f.value.document_id for f in focus.matches(world.customer, user, extract, EVERYTHING)}
    as_organisation = {f.value.document_id for f in focus.matches(world.customer, None, extract, EVERYTHING)}
    assert world.deed in as_organisation and world.deed not in as_person
