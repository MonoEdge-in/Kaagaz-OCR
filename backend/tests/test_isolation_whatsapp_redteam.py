"""W7, the red team (#603, PRD 7 section 15): a button or row id is only text the phone sends back,
so a person can send any id, even one made for someone else. Ids forged from another person's chat,
in another organisation, reach nothing of theirs:

* another person's "Did you mean" choice: "That choice isn't available any more", and Ask is not
  asked;
* another person's welcome [Not me]: neither connection ends;
* another person's folder row: "That list has changed", and nothing is saved in their folder.
"""

from collections.abc import Iterator

import pytest

from kaagaz.adapters.whatsapp import NOT_ME
from kaagaz.channel.texts import say
from kaagaz.db.engine import Database
from tests.test_operator_auth_db import _owner
from tests.test_whatsapp_chat_db import _clarified, _World
from tests.test_whatsapp_upload_db import World, pdf, row_named
from tests.whatsapp_fake import WhatsAppFake, message, running_fake

pytestmark = pytest.mark.db


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def test_another_persons_did_you_mean_choice_reaches_nothing_of_theirs(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    theirs, mine = world.person(), world.person()  # two organisations
    document = world.api.add_document(theirs.c, name="pune land.pdf", uploaded_by=theirs.u)
    asked = _clarified(world, theirs, document)
    gone = (say("choice_gone", "en"), ["another.en", "menu.en"])
    assert world.tap(mine, f"pick:{asked}:c1:en") == gone  # no conversation of their own yet
    world.say(mine, "What is the total?")  # now with one
    assert world.tap(mine, f"pick:{asked}:c1:en") == gone
    assert world.api.answers.resumed == []
    assert all("pune" not in repr(each).casefold() for each in world.replies(mine))


def test_another_connections_not_me_ends_neither(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    theirs, mine = world.person(), world.person()
    button = {**message(mine.number, world.api.clock()), "type": "button"}
    button["button"] = {"payload": NOT_ME + theirs.link}
    assert world.send(mine, button)[1] == ["ask.en", "upload.en", "get.en"]  # the menu, nothing more
    ended = _owner(
        db,
        "SELECT ended_at FROM whatsapp_links WHERE id = ANY(CAST(:l AS uuid[]))",
        {"l": [theirs.link, mine.link]},
    )
    assert ended == [(None,), (None,)]


def test_another_persons_folder_row_saves_nothing_in_their_folder(db: Database, fake: WhatsAppFake) -> None:
    world = World(db, fake)
    theirs, mine = world.person(), world.person()
    world.folder(theirs, "Their Salaries")
    world.file(theirs, pdf("theirs"))
    world.tap(theirs, "up:choose::en")
    forged = row_named(world, theirs, "Their Salaries")
    world.file(mine, pdf("mine"), "mine.pdf")
    world.tap(mine, "up:choose::en")
    body, _ = world.tap(mine, forged)
    assert body.startswith(say("list_changed", "en"))
    assert world.documents(theirs) == [] and world.documents(mine) == []
    assert all("salaries" not in repr(each).casefold() for each in world.replies(mine))
