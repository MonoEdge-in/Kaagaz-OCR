"""Kaagaz on WhatsApp reaches only what the person can see (#603 W4, PRD 7 section 15 "Tests and
done when"), on PRD 7's walled organisation (``_ask_org``: PR 8's, with the real AnswerService),
driven through the fake WhatsApp, the webhook and the channel in the API's role.

* The plain reviewer and Finance's member each ask about, search for and request a file hidden
  from them: nothing of it reaches a reply (its id, pieces, name, figures, names, places or topic),
  and the reply is the one a file that never was gets; Finance's member still finds Finance's file.
* The same question in the browser gives the same answer.
* A locked file is never named, even to the person who may open it (who sees it in the browser).
* "Did you mean" offers no name from a hidden file, nor one only a locked file holds.
* Aadhaar, PAN, bank and card numbers never reach WhatsApp in full (the browser shows them).
"""

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.answering import rewrite
from kaagaz.answering.service import AnswerService
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.texts import MASK, say
from kaagaz.db.engine import Database
from tests.db_support import insert_key
from tests.test_answering_clarify import plan
from tests.test_answering_golden import NOW
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import _app
from tests.test_isolation_ask_views import AMOUNTS, HIDDEN, RED_TEAM, WORDS, AskOrg, _ask_org, _Recording
from tests.test_operator_auth_db import _owner
from tests.test_search_db import add_pieces, axis
from tests.whatsapp_fake import (
    NUMBER_KEY,
    WhatsAppFake,
    connect,
    delivery,
    message,
    new_number,
    post,
    running_fake,
)
from tests.whatsapp_fake import whatsapp_settings as settings_for

pytestmark = pytest.mark.db

NOT_FOUND = say("not_found", "en")
ONLY_FILES = say("only_files", "en")


class _Model(_Recording):
    """PR 8's recording model; a question naming Ramesh is planned with him as a person, so Ask asks
    "Which Ramesh do you mean?" from the vocabulary the asker may see."""

    def chat_json(self, *, system: str, user: str, schema: Any, **kwargs: Any) -> dict[str, Any]:
        if system == rewrite.SYSTEM_PROMPT and "Ramesh" in user:
            self.prompts.append(f"{system}\n{user}")
            question = user.split("<question>\n", 1)[1].split("\n</question>", 1)[0]
            return {"question": question, "aspects": [], "plan": plan(people=["Ramesh"])}
        return super().chat_json(system=system, user=user, schema=schema, **kwargs)


@dataclass
class Walled:
    ask: AskOrg
    fake: WhatsAppFake
    channel: Channel
    model: _Model
    numbers: dict[str, str]  # person: their connected number

    def send(self, person: str, item: dict[str, Any]) -> Any:
        """One message from ``person``'s number, handled; the reply to it (as ``fake.sent`` shows it)."""
        assert post(self.ask.api.client(), delivery(item)).status_code == 200
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)
        to = self.numbers[person].removeprefix("+")
        return [what for number, what in self.fake.sent() if number == to][-1]

    def say(self, person: str, text: str, *, fresh: bool = True) -> Any:
        if fresh:  # no follow-up: each question starts its own conversation, as in the browser
            _owner(self.ask.db, "UPDATE messages SET created_at = created_at - interval '31 minutes'", {})
        return self.send(person, message(self.numbers[person], self.ask.api.clock(), text))

    def tap(self, person: str, tapped: str) -> Any:
        item = {
            "from": self.numbers[person].removeprefix("+"),
            "id": f"wamid.{uuid.uuid4().hex}",
            "timestamp": str(int(self.ask.api.clock().timestamp())),
            "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": tapped, "title": "x"}},
        }
        return self.send(person, item)

    def browser(self, person: str, question: str) -> dict[str, Any]:
        client = self.ask.org.people[person].client
        reply = client.post("/api/chat", json={"question": question}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return dict(reply.json()["answer"])

    def sent(self) -> str:
        """Everything Meta was asked to send, as one text."""
        return json.dumps([body for _, _, body in self.fake.requests], ensure_ascii=False).casefold()


@pytest.fixture
def walled(db: Database) -> Iterator[Walled]:
    with running_fake() as fake:
        model = _Model()
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
        yield Walled(ask, fake, channel, model, numbers)


def _marks(ask: AskOrg, person: str) -> list[str]:
    """What of ``person``'s hidden files must never reach WhatsApp."""
    marks = []
    for name in HIDDEN[person]:
        word = WORDS[name]
        marks += [ask.id(name), *ask.pieces[name], f"{name}.pdf", str(AMOUNTS[name]), f"invoice {word}"]
        marks += [f"seller {word}", f"ramesh {word}", f"{word}pur", f"topic {word}"]
    return marks


def _named(walled: Walled, marks: list[str]) -> list[str]:
    """The ``marks`` found as whole words in anything sent (an amount may sit inside a random id)."""
    sent = walled.sent()
    return [m for m in marks if re.search(f"(?<![0-9a-z]){re.escape(m.casefold())}(?![0-9a-z])", sent)]


def _answer_text(body: str) -> str:
    """The answer's own text in a WhatsApp reply: before its sources, without ONLY_FILES."""
    return body.split("\n\n", 1)[0].removesuffix(f"\n{ONLY_FILES}")


@pytest.mark.parametrize("person", ["plain", "member"])
def test_a_hidden_file_asked_searched_or_requested_gives_nothing_and_no_hint(
    walled: Walled, person: str
) -> None:
    ask = walled.ask
    for question in RED_TEAM:
        walled.say(person, question)
    hidden = HIDDEN[person][0]  # fin for the plain reviewer, sal for Finance's member
    walled.tap(person, "get.en")
    searched = walled.say(person, WORDS[hidden], fresh=False)  # its own word (others may match it loosely)
    requested = walled.tap(person, f"file:{ask.id(hidden)}:en")
    made_up = walled.tap(person, f"file:{uuid.uuid4()}:en")
    assert requested == made_up == (NOT_FOUND, ["menu.en"])
    assert _named(walled, _marks(ask, person)) == []
    # Ask itself read nothing hidden either (PR 8's red team, here through WhatsApp).
    prompts = "\n".join(walled.model.prompts + walled.model.embedded).casefold()
    assert [
        m for m in _marks(ask, person) if re.search(f"(?<![0-9a-z]){re.escape(m)}(?![0-9a-z])", prompts)
    ] == []
    if person == "member":  # the control: Finance's member finds Finance's own file
        walled.tap(person, "get.en")
        assert walled.say(person, WORDS["fin"], fresh=False)[0].startswith("fin.pdf\n")
    # The differential: the same search once the hidden file is gone for good gives the same reply.
    _owner(ask.db, "DELETE FROM documents WHERE id = :d", {"d": ask.id(hidden)})
    walled.tap(person, "get.en")
    assert walled.say(person, WORDS[hidden], fresh=False) == searched


@pytest.mark.parametrize("person", ["member", "plain"])
def test_the_same_question_in_the_browser_gives_the_same_answer(walled: Walled, person: str) -> None:
    """The plain reviewer may open the locked file (a lock grant): shortcut, until PR 12 stops Ask
    reading locked files, a fixed query's totals and counts are "couldn't find" for them on WhatsApp."""
    for question in RED_TEAM:
        body, _ = walled.say(person, question)
        _owner(walled.ask.db, "UPDATE messages SET answer = answer - 'repeat_key'", {})  # answered afresh
        browser = walled.browser(person, question)
        granted = person == "plain" and browser["answer"]["route"] == "fixed_query"
        assert _answer_text(body) == (NOT_FOUND if granted else browser["text"]), question


def test_a_locked_file_is_never_named_even_for_the_person_who_may_open_it(walled: Walled) -> None:
    ask = walled.ask
    locked = ask.id("locked")  # in Sales, locked; the plain reviewer may open it (a lock grant)
    piece = add_pieces(ask.db, ask.org.c, locked, [("Invoice zebra register", axis(9))])
    with ask.db.customer(ask.org.c) as conn:
        insert_key(conn, ask.org.c, locked, "person", "Ramesh zebra")
    browser = walled.browser("plain", "Tell me about zebra")
    assert locked in json.dumps(browser)  # the browser shows it to them: WhatsApp never does
    asked = walled.say("plain", "Tell me about zebra")
    walled.tap("plain", "get.en")
    searched = walled.say("plain", "zebra", fresh=False)
    requested = walled.tap("plain", f"file:{locked}:en")
    assert asked[0] == NOT_FOUND and searched == requested == (NOT_FOUND, ["menu.en"])
    assert _named(walled, [locked, *piece, "locked.pdf", "invoice zebra", "ramesh zebra"]) == []


def test_did_you_mean_offers_no_name_of_a_hidden_or_locked_file(walled: Walled) -> None:
    ask = walled.ask
    with ask.db.customer(ask.org.c) as conn:  # a name only the locked file holds
        insert_key(conn, ask.org.c, ask.id("locked"), "person", "Ramesh zebra")
    browser = walled.browser("plain", "What land does Ramesh own?")
    offered = [c["label"] for c in browser["answer"]["clarify"]["choices"]]
    assert "Ramesh zebra" in offered and "Ramesh aardvark" not in offered  # the browser: PR 8's views
    body, choices = walled.say("plain", "What land does Ramesh own?")
    assert body == "Which Ramesh do you mean?" and len(choices) == 3
    raw = walled.fake.requests[-1][2]["interactive"]["action"]["buttons"]
    titles = [b["reply"]["title"] for b in raw]
    assert sorted(titles[:2]) == ["Ramesh dingo", "Ramesh emu"] and titles[2] == "Something else"
    hidden = [f"ramesh {WORDS[name]}" for name in HIDDEN["plain"]] + ["ramesh zebra"]
    assert _named(walled, hidden) == []
    answered = walled.tap("plain", choices[0])  # a choice tapped is answered by Ask
    assert answered[1] == ["another.en", "menu.en"]


def test_a_file_given_to_the_person_says_shared_with_you_never_its_hidden_folder(walled: Walled) -> None:
    """Finance's "given" file, shared with the plain reviewer alone: its card names no Finance; a file
    of a folder they see shows its path."""
    ask = walled.ask
    given = walled.tap("plain", f"file:{ask.id('given')}:en")[0].split("\n")
    assert given[0] == "given.pdf" and given[1].startswith("Shared with you · ")
    assert "finance" not in walled.sent()
    sales = walled.tap("plain", f"file:{ask.id('sales')}:en")[0].split("\n")
    assert sales[1].startswith("Sales · ")


def test_id_numbers_never_reach_whatsapp_in_full(walled: Walled) -> None:
    ask = walled.ask
    card = ask.api.add_document(ask.org.c, name="kyc.pdf", folder_id=ask.org.folders["sales"])
    add_pieces(
        ask.db, ask.org.c, card, [("Aadhaar 1234 5678 9012, PAN ABCDE1234F, account 001234567890", axis(0))]
    )
    question = "What is the Aadhaar, PAN and account number?"
    browser = walled.browser("plain", question)
    assert "1234 5678 9012" in browser["text"]  # the browser shows it in full
    body, _ = walled.say("plain", question)
    assert f"{MASK}9012" in body and f"{MASK}234F" in body and f"{MASK}7890" in body
    assert say("masked", "en") in body
    walled.tap("plain", "get.en")
    walled.say("plain", "Aadhaar", fresh=False)
    sent = walled.sent()
    assert not any(full in sent for full in ("1234 5678 9012", "abcde1234f", "001234567890"))
