"""Kaagaz on WhatsApp's chat (#603 W4, PRD 7 section 15 "The chat, step by step"), end to end on the
fake WhatsApp: a signed message through the webhook, the channel in the API's role, one reply.

The start menu (in the language of the message, the welcome's [Start] too) and its More list; Ask
as the person (the browser's Ask, the conversation marked "From WhatsApp", audited ``via``), its
sources and links, a long answer cut with a link to the whole, a follow-up within 30 minutes, a
message handled again answered from its saved answer; "Did you mean" as buttons or a list, a tap
and a typed reply answered by Ask, a choice gone or expired, "Something else", a locked choice left
out; Get a file: a card, a list, nothing; a hidden, locked, missing or made-up file the same
"couldn't find"; the limits in plain words; Hindi answered in Hindi unless the numbers change;
ID numbers masked."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.channel import chat as chat_module
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.texts import say
from kaagaz.db.engine import Database
from tests.test_api_chat import _cited_answer
from tests.test_api_chat_clarify import _clarify
from tests.test_api_support import CSRF, Api, Signed, answer_json
from tests.test_operator_auth_db import _owner
from tests.test_search_db import add_pieces
from tests.whatsapp_fake import (
    APP_URL,
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


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


@dataclass
class Person:
    signed: Signed
    number: str
    link: str

    @property
    def c(self) -> str:
        return self.signed.customer_id

    @property
    def u(self) -> str:
        return self.signed.user_id


def tap(number: str, sent_at: Any, tapped: str, *, row: bool = False) -> dict[str, Any]:
    """A reply button (or, ``row``, a list row) tapped, as Meta delivers it."""
    kind = "list_reply" if row else "button_reply"
    return {
        "from": number.removeprefix("+"),
        "id": f"wamid.{uuid.uuid4().hex}",
        "timestamp": str(int(sent_at.timestamp())),
        "type": "interactive",
        "interactive": {"type": kind, kind: {"id": tapped, "title": "x"}},
    }


class _World:
    def __init__(self, db: Database, fake: WhatsAppFake, *, model: Any = None, **settings: Any) -> None:
        self.db, self.fake = db, fake
        self.api = Api(db, settings=settings_for(fake, **settings), model=model)
        self.client = self.api.client()
        self.chat = Chat(self.api.app.state.kaagaz)  # the API's own context: the browser's Ask
        self.channel = Channel(
            db=self.api.app_db,
            sender=MetaWhatsApp.from_settings(self.api.settings),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
            chat=self.chat,
        )

    def person(self, customer: str | None = None, role: str = "member") -> Person:
        if customer is None:
            customer, _ = self.api.customer()
        signed = self.api.sign_in(customer, role)
        number = new_number()
        return Person(signed, number, connect(self.db, customer, signed.user_id, number))

    def send(self, person: Person, *messages: dict[str, Any]) -> Any:
        """Post ``messages`` (texts or taps), run the channel, and give the last reply to ``person``."""
        assert post(self.client, delivery(*messages)).status_code == 200
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)
        return self.replies(person)[-1]

    def say(self, person: Person, text: str) -> Any:
        return self.send(person, message(person.number, self.api.clock(), text))

    def tap(self, person: Person, tapped: str, *, row: bool = False) -> Any:
        return self.send(person, tap(person.number, self.api.clock(), tapped, row=row))

    def replies(self, person: Person) -> list[Any]:
        return [what for to, what in self.fake.sent() if to == person.number.removeprefix("+")]

    def raw(self, person: Person) -> dict[str, Any]:
        """The last message sent to ``person`` as Meta got it."""
        bodies = [body for _, _, body in self.fake.requests if body["to"] == person.number.removeprefix("+")]
        return dict(bodies[-1])


@pytest.fixture
def world(db: Database, fake: WhatsAppFake) -> _World:
    return _World(db, fake)


# --- the menu ------------------------------------------------------------------------------


def test_the_menu_is_in_the_language_of_the_message_and_after_the_welcomes_start(world: _World) -> None:
    person = world.person()
    assert world.say(person, "Hi") == (
        "Hello Test. What would you like to do?",
        ["ask.en", "get.en", "more.en"],
    )
    marathi = world.say(person, "नमस्कार")
    assert marathi == (say("menu", "mr", "Test"), ["ask.mr", "get.mr", "more.mr"])
    start = {**message(person.number, world.api.clock()), "type": "button", "button": {"payload": "start"}}
    assert world.send(person, start)[1] == ["ask.en", "get.en", "more.en"]
    assert world.tap(person, "more.hi", row=False) == (
        say("more", "hi"),
        ["menu.en", "menu.hi", "menu.mr", "help.hi", "disconnect"],
    )
    assert world.tap(person, "menu.mr", row=True)[1] == ["ask.mr", "get.mr", "more.mr"]  # a language picked
    assert world.tap(person, "made up")[1] == ["ask.en", "get.en", "more.en"]  # an unknown tap: the menu
    assert world.tap(person, "ask.mr") == say("ask", "mr")
    assert world.tap(person, "disconnect", row=True)[1] == ["disconnect_yes", "keep"]  # W3's, asked first
    assert world.tap(person, "help.en")[0] == say("help", "en", APP_URL)


def test_a_photo_or_a_voice_note_gets_type_only_and_a_long_question_is_refused(world: _World) -> None:
    person = world.person()
    photo = {**message(person.number, world.api.clock()), "type": "image", "image": {"id": "media.1"}}
    assert world.send(person, photo) == say("typed_only", "en")
    assert world.say(person, "x" * 1001) == say("too_long", "en")
    assert world.api.answers.asked == []


def test_menu_leaves_which_file(world: _World) -> None:
    person = world.person()
    world.tap(person, "get.en")
    world.say(person, "Menu")
    world.say(person, "electricity")
    assert world.api.answers.asked == [(person.c, "electricity")]  # a question, not a file's name


# --- Ask -----------------------------------------------------------------------------------


def test_a_question_is_the_browsers_ask_as_the_person_with_its_sources_and_links(world: _World) -> None:
    person = world.person()
    document = world.api.add_document(person.c, name="bill 1.pdf", uploaded_by=person.u)
    world.api.answers.reply = _cited_answer(document)
    body, buttons = world.say(person, "What is the total?")
    link = f"{APP_URL}/documents/{document}?page=1"
    assert body == f"Sentence 1. All of them.\n\nSource: bill 1.pdf, page 1\nOpen: {link}"
    assert buttons == ["another.en", "menu.en"]
    assert world.api.answers.asked == [(person.c, "What is the total?")]
    assert world.raw(person)["interactive"]["type"] == "button"  # one message, no file, no media
    listed = person.signed.client.get("/api/conversations").json()["items"]
    assert [item["channel"] for item in listed] == ["whatsapp"]  # history: "From WhatsApp"
    (asked,) = [row for row in world.api.audit_rows(person.c) if row[0] == "chat.ask"]
    assert asked[3]["via"] == "whatsapp"
    browser = person.signed.client.post("/api/chat", json={"question": "And again?"}, headers=CSRF).json()
    listed = person.signed.client.get("/api/conversations").json()["items"]
    assert {item["id"]: item["channel"] for item in listed}[browser["conversation_id"]] is None


def test_a_follow_up_within_30_minutes_continues_the_conversation(world: _World) -> None:
    person = world.person()
    world.say(person, "What is the total?")
    world.say(person, "And the tax?")
    assert len(person.signed.client.get("/api/conversations").json()["items"]) == 1
    assert [len(h) for h in world.api.answers.histories] == [0, 1]
    _owner(world.db, "UPDATE messages SET created_at = created_at - interval '31 minutes'", {})
    world.say(person, "And the date?")
    assert len(person.signed.client.get("/api/conversations").json()["items"]) == 2
    assert len(world.api.answers.histories[-1]) == 0


def test_a_message_handled_again_gets_its_saved_answer_with_no_second_question(world: _World) -> None:
    person = world.person()
    world.fake.failures = [500]  # the reply could not be sent: the message waits
    assert (
        post(world.client, delivery(message(person.number, world.api.clock(), "Total?"))).status_code == 200
    )
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    assert world.replies(person) == []
    _owner(world.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE id = :l", {"l": person.link})
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    assert world.replies(person)[-1][0].startswith("The total is 1,200.")
    assert len(world.api.answers.asked) == 1


def test_a_saved_answer_whose_source_was_hidden_since_is_not_sent_nor_said_to_be_hidden(
    world: _World,
) -> None:
    """Handled again after its source went (Ask's saved reply is replaced, PR 8): "couldn't find",
    never Ask's "You no longer have access to a document behind this answer"."""
    person = world.person()
    document = world.api.add_document(person.c, uploaded_by=person.u)
    world.api.answers.reply = _cited_answer(document)
    world.fake.failures = [500]
    assert (
        post(world.client, delivery(message(person.number, world.api.clock(), "Total?"))).status_code == 200
    )
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    _owner(world.db, "DELETE FROM documents WHERE id = :d", {"d": document})
    _owner(world.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE id = :l", {"l": person.link})
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    assert world.replies(person) == [(NOT_FOUND, ["another.en", "menu.en"])]
    assert len(world.api.answers.asked) == 1


def test_a_long_answer_is_cut_with_a_link_to_the_whole_one(world: _World) -> None:
    person = world.person()
    world.api.answers.reply = {**answer_json(), "sentences": [{"text": "word " * 400, "citations": []}]}
    body, _ = world.say(person, "Tell me everything")
    conversation = person.signed.client.get("/api/conversations").json()["items"][0]["id"]
    assert body.endswith(f"…\nFull answer in Kaagaz: {APP_URL}/chat?c={conversation}")
    assert len(body) <= chat_module.MAX_ANSWER_CHARS + 100


def test_nothing_found_says_only_kaagaz_files(world: _World) -> None:
    person = world.person()
    world.api.answers.reply = answer_json(status="not_found")
    body, _ = world.say(person, "Write me a poem about the sea")
    assert body == "Nothing in your documents matches this yet.\nI can only help with your Kaagaz files."


def test_the_limits_in_plain_words(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake, chat_questions_per_minute=1)
    person = world.person()
    world.say(person, "What is the total?")
    assert world.say(person, "And the tax?") == say("too_many", "en")
    _owner(
        db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, 'ask', false)",
        {"c": person.c},
    )
    other = world.person(person.c)
    assert world.say(other, "What is the total?") == say("ask_off", "en")


# --- "Did you mean" ------------------------------------------------------------------------


def _clarified(world: _World, person: Person, *documents: str) -> str:
    """A "Did you mean" asked; its message id."""
    world.api.answers.reply = _clarify(*documents)
    world.say(person, "Land in Puny?")
    world.api.answers.reply = answer_json()
    conversation = person.signed.client.get("/api/conversations").json()["items"][0]["id"]
    shown = person.signed.client.get(f"/api/conversations/{conversation}").json()["messages"]
    return str(shown[-1]["id"])


def test_did_you_mean_is_buttons_and_a_tap_is_answered_by_ask(world: _World) -> None:
    person = world.person()
    pune, panvel = (
        world.api.add_document(person.c, name=n, uploaded_by=person.u) for n in ("p.pdf", "v.pdf")
    )
    asked = _clarified(world, person, pune, panvel)
    ids = [f"pick:{asked}:{c}:en" for c in ("c1", "c2", "other")]
    assert world.replies(person)[-1] == ("Did you mean Pune?", ids)
    assert world.tap(person, ids[2]) == say("type_it", "en")  # "Something else": type it
    assert world.tap(person, ids[0])[0].startswith("The total is 1,200.")
    assert [pending.picked for pending in world.api.answers.resumed] == [("Pune",)]
    assert world.tap(person, ids[1]) == (say("choice_gone", "en"), ["another.en", "menu.en"])  # answered
    assert len(world.api.answers.resumed) == 1


def test_a_typed_reply_answers_the_open_did_you_mean(world: _World) -> None:
    person = world.person()
    _clarified(world, person, world.api.add_document(person.c, uploaded_by=person.u))
    world.say(person, "Pune")
    assert [pending.typed for pending in world.api.answers.resumed] == ["Pune"]


def test_a_choice_tapped_after_30_minutes_is_gone(world: _World) -> None:
    person = world.person()
    asked = _clarified(world, person, world.api.add_document(person.c, uploaded_by=person.u))
    _owner(world.db, "UPDATE messages SET created_at = created_at - interval '31 minutes'", {})
    assert world.tap(person, f"pick:{asked}:c1:en")[0] == say("choice_gone", "en")
    assert world.tap(person, "pick:not-an-id:c1:en")[0] == say("choice_gone", "en")
    assert world.api.answers.resumed == []


def test_more_than_three_choices_are_a_list_and_a_locked_files_choice_is_left_out(world: _World) -> None:
    person = world.person()
    files = [world.api.add_document(person.c, uploaded_by=person.u) for _ in range(5)]
    reply = _clarify(files[0])
    picks = [
        {"id": f"c{n}", "label": f"Place {n}", "kind": "pick", "document_ids": [d]}
        for n, d in enumerate(files, 1)
    ]
    every = {"id": "all", "label": "All of these", "kind": "all"}
    other = {"id": "other", "label": "Something else", "kind": "other"}
    reply["clarify"] = {**reply["clarify"], "choices": [*picks, every, other], "multi": True}
    world.api.answers.reply = reply
    world.say(person, "Land between A and B?")
    raw = world.raw(person)["interactive"]
    rows = raw["action"]["sections"][0]["rows"]
    places = [f"Place {n}" for n in range(1, 6)]
    assert raw["type"] == "list" and [r["title"] for r in rows] == [*places, "All of these", "Something else"]
    _lock(world, person, files[4])
    world.say(person, "Land between A and B again?")
    rows = world.raw(person)["interactive"]["action"]["sections"][0]["rows"]
    assert [r["title"] for r in rows] == [*(f"Place {n}" for n in range(1, 5)), "Something else"]


# --- Get a file ------------------------------------------------------------------------------


def _lock(world: _World, person: Person, document: str) -> None:
    """``document`` locked, and ``person`` allowed to open it (PRD 7's lock grant)."""
    params = {"c": person.c, "d": document, "u": person.u}
    _owner(world.db, "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d", params)
    _owner(
        world.db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        params,
    )


def _filed(world: _World, person: Person, name: str, words: str, **columns: Any) -> str:
    document = world.api.add_document(person.c, name=name, uploaded_by=person.u, **columns)
    add_pieces(world.db, person.c, document, [(words, None)])
    return document


def test_get_a_file_one_match_is_its_card_with_a_link(world: _World) -> None:
    person = world.person()
    folder = world.api.add_folder(person.c, person.u, "Bills")
    document = _filed(
        world, person, "MSEB March.pdf", "electricity bill march", folder_id=folder, page_count=2
    )
    _owner(
        world.db,
        "UPDATE documents SET summary = 'Electricity bill for March.', doc_type = 'invoice',"
        " created_at = '2026-09-27 10:00+00' WHERE id = :d",
        {"d": document},
    )
    assert world.tap(person, "get.en") == say("which_file", "en")
    body, buttons = world.say(person, "electricity")
    assert body == (
        "MSEB March.pdf\nBills · Invoice · 2 pages · added 27 Sep 2026\n"
        f"Summary: Electricity bill for March.\nOpen in Kaagaz: {APP_URL}/documents/{document}"
    )
    assert buttons == ["not_this.en", "menu.en"]
    assert ("whatsapp.file_link", "document", document, {"via": "whatsapp"}) in world.api.audit_rows(person.c)
    world.api.answers.reply = answer_json()
    world.say(person, "electricity")  # the card ended the step: a question now
    assert world.api.answers.asked[-1][1] == "electricity"


def test_get_a_file_several_matches_are_a_list_and_a_row_tapped_is_its_card(world: _World) -> None:
    person = world.person()
    files = [_filed(world, person, f"bill {n}.pdf", f"water bill {n}") for n in range(3)]
    world.tap(person, "get.en")
    body, rows = world.say(person, "water")
    assert body == "I found 3 files. Pick one:\nNot here? Add a year, name or amount."
    assert sorted(rows) == sorted(f"file:{d}:en" for d in files)
    assert world.raw(person)["interactive"]["action"]["button"] == "See files"
    assert world.tap(person, f"file:{files[1]}:en", row=True)[0].startswith("bill 1.pdf\n")
    assert world.tap(person, "not_this.en") == (say("not_here", "en"))
    assert world.say(person, "nothing like it") == (NOT_FOUND, ["menu.en"])


def test_a_hidden_locked_missing_or_made_up_file_gets_the_same_reply(world: _World) -> None:
    person = world.person()
    other = world.person()  # another organisation
    theirs = _filed(world, other, "theirs.pdf", "secret ledger")
    locked = _filed(world, person, "locked.pdf", "secret ledger")
    _lock(world, person, locked)  # they may open it in Kaagaz
    binned = _filed(
        world, person, "binned.pdf", "secret ledger", deleted_at=world.api.clock(), deleted_by=person.u
    )
    taps = [f"file:{d}:en" for d in (theirs, locked, binned, str(uuid.uuid4()))] + ["file:junk:en"]
    assert [world.tap(person, each, row=True) for each in taps] == [(NOT_FOUND, ["menu.en"])] * 5
    world.tap(person, "get.en")
    assert world.say(person, "secret ledger") == (NOT_FOUND, ["menu.en"])
    sent = repr(world.fake.requests)
    assert not any(
        mark in sent for mark in ("locked.pdf", "binned.pdf", "theirs.pdf", locked, binned, theirs)
    )
    assert "whatsapp.file_link" not in [row[0] for row in world.api.audit_rows(person.c)]


def test_a_card_masks_id_numbers(world: _World) -> None:
    person = world.person()
    document = _filed(world, person, "Aadhaar 1234 5678 9012.pdf", "identity card")
    summary = "PAN ABCDE1234F, card 4111-1111-1111-1111."
    _owner(world.db, "UPDATE documents SET summary = :s WHERE id = :d", {"s": summary, "d": document})
    world.tap(person, "get.en")
    body, _ = world.say(person, "identity")
    assert "Aadhaar ••••9012.pdf" in body and "PAN ••••234F" in body
    assert "card ••••1111." in body and say("masked", "en") in body
    assert "1234 5678" not in body and "ABCDE" not in body and "4111-1111" not in body


def test_an_answers_id_numbers_are_masked(world: _World) -> None:
    person = world.person()
    world.api.answers.reply = {
        **answer_json(),
        "sentences": [
            {"text": "The account number is 001234567890 and the PAN is ABCDE1234F.", "citations": []}
        ],
    }
    body, _ = world.say(person, "What is my account number?")
    assert body.startswith("The account number is ••••7890 and the PAN is ••••234F.")
    assert say("masked", "en") in body


# --- the language of the answer ----------------------------------------------------------------


class _Translator:
    """The model: a translation of the answer, its numbers kept or (``wrong``) changed."""

    def __init__(self, wrong: bool = False) -> None:
        self.wrong, self.asked = wrong, 0

    def chat_json(self, *, system: str, user: str, **_: Any) -> dict[str, Any]:
        self.asked += 1
        return {"text": "एकूण रक्कम 1,300 आहे." if self.wrong else "एकूण रक्कम 1,200 आहे."}

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("no embedding here")


@pytest.mark.parametrize("wrong", [False, True])
def test_a_marathi_question_is_answered_in_marathi_unless_a_number_changes(
    db: Database, fake: WhatsAppFake, wrong: bool
) -> None:
    translator = _Translator(wrong)
    world = _World(db, fake, model=translator)
    person = world.person()
    body, buttons = world.say(person, "एकूण रक्कम किती आहे?")
    assert translator.asked == 1 and buttons == ["another.mr", "menu.mr"]
    assert body == ("The total is 1,200." if wrong else "एकूण रक्कम 1,200 आहे.")
    world.say(person, "What is the total?")
    assert translator.asked == 1  # English: no call
