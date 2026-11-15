"""Kaagaz on WhatsApp's voice notes reach only what the person can see (#603 W6), on PRD 7's walled
organisation (``_ask_org``, with the real AnswerService), through the fake WhatsApp, the webhook and
the channel in the API's role, as W4's typed questions (tests/test_isolation_whatsapp_chat.py).

* The plain reviewer and Finance's member each ask PR 8's red-team questions by voice, and say a
  hidden file's word after "Which file?": nothing of a hidden file reaches a reply, every reply after
  its "I heard" line is the reply to the same words typed, and the search is the one a file that
  never was gets.
* A locked file is never named by voice either, even to the person who may open it (the reply Ask
  made from it is replaced just before it goes, "I heard" kept).
* ID numbers never reach WhatsApp in full, in "I heard" or the answer.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.answering.service import AnswerService
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.texts import MASK
from kaagaz.db.engine import Database
from tests.db_support import insert_key
from tests.test_answering_golden import NOW
from tests.test_api_support import Api
from tests.test_isolation_access_views import _app
from tests.test_isolation_ask_views import HIDDEN, RED_TEAM, WORDS, _ask_org
from tests.test_isolation_whatsapp_chat import NOT_FOUND, Walled, _answer_text, _marks, _Model, _named
from tests.test_operator_auth_db import _owner
from tests.test_search_db import add_pieces, axis
from tests.whatsapp_fake import (
    NUMBER_KEY,
    connect,
    new_number,
    ogg_opus,
    running_fake,
    voice_message,
)
from tests.whatsapp_fake import whatsapp_settings as settings_for

pytestmark = pytest.mark.db


class _Hearing(_Model):
    """The walled organisation's model, which also writes each voice note down as ``heard``."""

    def __init__(self) -> None:
        super().__init__()
        self.heard = ""

    def transcribe(self, **_: Any) -> dict[str, Any]:
        return {"text": self.heard, "language": "en", "sure": True}


class Spoken(Walled):
    model: _Hearing

    def speak(self, person: str, words: str, *, fresh: bool = True) -> Any:
        """``words`` said in a voice note by ``person``; the reply to it."""
        if fresh:  # no follow-up, as ``say``
            _owner(self.ask.db, "UPDATE messages SET created_at = created_at - interval '31 minutes'", {})
        self.model.heard = words
        media = self.fake.add_media(ogg_opus(4))
        return self.send(person, voice_message(self.numbers[person], self.ask.api.clock(), media))


@pytest.fixture
def walled(db: Database) -> Iterator[Spoken]:
    with running_fake() as fake:
        model = _Hearing()
        settings = settings_for(fake, model_embed="test-embed")
        answers = AnswerService(db=_app(db), model=model, settings=settings, clock=lambda: NOW)
        api = Api(db, settings=settings, answers=answers, model=model)
        ask = _ask_org(api)
        numbers = {}
        for person in ("plain", "member"):
            numbers[person] = new_number()
            connect(db, ask.org.c, ask.user(person), numbers[person])
        channel = Channel(
            db=api.app_db,
            sender=MetaWhatsApp.from_settings(settings),
            number_key=NUMBER_KEY,
            clock=api.clock,
            chat=Chat(api.app.state.kaagaz),
        )
        yield Spoken(ask, fake, channel, model, numbers)


def _after_heard(body: str, words: str) -> str:
    heard = f'I heard: "{words}"\n\n'
    assert body.startswith(heard)
    return body.removeprefix(heard)


@pytest.mark.parametrize("person", ["plain", "member"])
def test_a_hidden_file_asked_by_voice_gives_nothing_and_the_typed_reply(walled: Spoken, person: str) -> None:
    for question in RED_TEAM:
        spoken, _ = walled.speak(person, question)
        typed, _ = walled.say(person, question)
        assert _answer_text(_after_heard(spoken, question)) == _answer_text(typed), question
    hidden = HIDDEN[person][0]
    walled.tap(person, "get.en")
    searched = walled.speak(person, WORDS[hidden], fresh=False)
    walled.tap(person, "get.en")
    typed = walled.say(person, WORDS[hidden], fresh=False)
    assert (_after_heard(searched[0], WORDS[hidden]), searched[1]) == typed
    assert _named(walled, _marks(walled.ask, person)) == []
    # The differential: the same search once the hidden file is gone for good gives the same reply.
    _owner(walled.ask.db, "DELETE FROM documents WHERE id = :d", {"d": walled.ask.id(hidden)})
    walled.tap(person, "get.en")
    assert walled.speak(person, WORDS[hidden], fresh=False) == searched
    if person == "member":  # the control: Finance's member finds Finance's own file by voice
        walled.tap(person, "get.en")
        assert _after_heard(walled.speak(person, WORDS["fin"], fresh=False)[0], WORDS["fin"]).startswith(
            "fin.pdf\n"
        )


def test_a_locked_file_is_never_named_by_voice(walled: Spoken) -> None:
    ask = walled.ask
    locked = ask.id("locked")  # the plain reviewer may open it (a lock grant)
    piece = add_pieces(ask.db, ask.org.c, locked, [("Invoice zebra register", axis(9))])
    with ask.db.customer(ask.org.c) as conn:
        insert_key(conn, ask.org.c, locked, "person", "Ramesh zebra")
    asked = walled.speak("plain", "Tell me about zebra")
    walled.tap("plain", "get.en")
    searched = walled.speak("plain", "zebra", fresh=False)
    assert _after_heard(asked[0], "Tell me about zebra") == NOT_FOUND
    assert _after_heard(searched[0], "zebra") == NOT_FOUND
    assert _named(walled, [locked, *piece, "locked.pdf", "invoice zebra", "ramesh zebra"]) == []


def test_id_numbers_never_reach_whatsapp_in_full_by_voice(walled: Spoken) -> None:
    ask = walled.ask
    card = ask.api.add_document(ask.org.c, name="kyc.pdf", folder_id=ask.org.folders["sales"])
    add_pieces(
        ask.db, ask.org.c, card, [("Aadhaar 1234 5678 9012, PAN ABCDE1234F, account 001234567890", axis(0))]
    )
    body, _ = walled.speak("plain", "Is the Aadhaar 1234 5678 9012 and the PAN ABCDE1234F?")
    assert body.startswith(f'I heard: "Is the Aadhaar {MASK}9012 and the PAN {MASK}234F?"')
    sent = walled.sent()
    assert not any(full in sent for full in ("1234 5678 9012", "abcde1234f", "001234567890"))
