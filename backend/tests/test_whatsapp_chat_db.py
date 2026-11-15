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
from kaagaz.api.errors import ApiError
from kaagaz.api.routers import chat as chat_router
from kaagaz.channel import chat as chat_module
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import NOT_CONNECTED, Channel
from kaagaz.channel.texts import say
from kaagaz.db import whatsapp_chat as wa_chat
from kaagaz.db.engine import Database
from kaagaz.db.whatsapp_chat import Current
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
    # Changed on purpose (#603 W5): [Ask a question] [Upload a file] [Get a file]; More is typed.
    assert world.say(person, "Hi") == (
        "Hello Test. What would you like to do?\nType More for language, help or to disconnect.",
        ["ask.en", "upload.en", "get.en"],
    )
    marathi = world.say(person, "नमस्कार")
    assert marathi == (say("menu", "mr", "Test"), ["ask.mr", "upload.mr", "get.mr"])
    start = {**message(person.number, world.api.clock()), "type": "button", "button": {"payload": "start"}}
    assert world.send(person, start)[1] == ["ask.en", "upload.en", "get.en"]
    assert world.tap(person, "more.hi", row=False) == (  # an older menu's [More]
        say("more", "hi"),
        ["menu.en", "menu.hi", "menu.mr", "help.hi", "disconnect"],
    )
    assert world.tap(person, "menu.mr", row=True)[1] == ["ask.mr", "upload.mr", "get.mr"]  # a language
    assert world.tap(person, "made up")[1] == ["ask.en", "upload.en", "get.en"]  # an unknown tap: the menu
    assert world.tap(person, "ask.mr") == say("ask_voice", "mr")  # Changed on purpose (#603 W6): voice too
    assert world.tap(person, "disconnect", row=True)[1] == ["disconnect_yes", "keep"]  # W3's, asked first
    assert world.tap(person, "help.en")[0] == say("help", "en", APP_URL)


def test_a_sticker_gets_type_only_and_a_long_question_is_refused(world: _World) -> None:
    person = world.person()
    # Changed on purpose (#603 W5, W6): a photo is a file to save now (tests/test_whatsapp_upload_db.py),
    # a voice note a question (tests/test_whatsapp_voice_db.py); a sticker is still typed only.
    sticker = {**message(person.number, world.api.clock()), "type": "sticker", "sticker": {"id": "1"}}
    assert world.send(person, sticker) == say("typed_only", "en")
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


@pytest.mark.parametrize("restarted", [False, True])
def test_a_saved_answer_whose_source_was_hidden_since_is_not_sent_nor_said_to_be_hidden(
    world: _World, restarted: bool
) -> None:
    """Handled again after its source went: the reply made is checked again just before it goes;
    after a restart (no reply held), Ask's saved reply is replaced (PR 8) and the chat says
    "couldn't find", never Ask's "You no longer have access to a document behind this answer"."""
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
    if restarted:
        world.channel.made.clear()
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    buttons = ["another.en", "menu.en"] if restarted else ["menu.en"]
    assert world.replies(person) == [(NOT_FOUND, buttons)]
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
    assert world.tap(person, "get.en") == say("which_file_voice", "en")  # Changed on purpose (#603 W6)
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


def test_a_locked_match_is_left_out_of_the_list_and_its_count(world: _World) -> None:
    person = world.person()
    seen = _filed(world, person, "seen.pdf", "gas bill")
    _lock(world, person, _filed(world, person, "locked.pdf", "gas bill"))
    world.tap(person, "get.en")
    body, buttons = world.say(person, "gas")
    assert body.startswith("seen.pdf\n") and buttons == ["not_this.en", "menu.en"]  # one match: its card
    assert "locked.pdf" not in repr(world.fake.requests) and seen in body


def test_a_list_row_is_masked_before_it_is_cut(world: _World) -> None:
    person = world.person()
    _filed(world, person, "My PAN and Aadhaar 1234 5678 9012.pdf", "kyc papers")
    _filed(world, person, "Other.pdf", "kyc papers")
    world.tap(person, "get.en")
    world.say(person, "kyc")
    rows = world.raw(person)["interactive"]["action"]["sections"][0]["rows"]
    assert "My PAN and Aadhaar ••••…" in [row["title"] for row in rows]
    assert "1234" not in repr(rows)


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


# --- review of W4 (#603): made once, checked again just before it goes ---------------------------


class _Meddler:
    """The model: its "translation" is the text as given, after ``meddle`` (an Admin changing a
    file while the reply is made)."""

    def __init__(self, meddle: Any) -> None:
        self.meddle, self.asked = meddle, 0
        self.seen: list[str] = []  # every text it was given

    def chat_json(self, *, system: str, user: str, **_: Any) -> dict[str, Any]:
        self.asked += 1
        self.seen.append(user)
        self.meddle()
        return {"text": user.split("<text>\n", 1)[1].rsplit("\n</text>", 1)[0]}

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("no embedding here")


# How a file goes from the person while their reply is made: held for someone else (hidden),
# locked, or moved to Trash.
CHANGES = {
    "hidden": "UPDATE documents SET held_for = :other WHERE id = :d",
    "locked": "UPDATE documents SET locked_at = now(), locked_by = :other WHERE id = :d",
    "trashed": "UPDATE documents SET deleted_at = now(), deleted_by = :other WHERE id = :d",
}


def _links(world: _World, person: Person) -> list[Any]:
    return [row for row in world.api.audit_rows(person.c) if row[0] == "whatsapp.file_link"]


def _run_again(world: _World, person: Person) -> None:
    """The connection's rest after a reply that was not sent is over: handle it again."""
    _owner(world.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE id = :l", {"l": person.link})
    for claim in world.channel.claim(100):
        world.channel.handle(claim)


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_card_whose_file_goes_while_it_is_made_is_not_sent(
    db: Database, fake: WhatsAppFake, change: str
) -> None:
    given: dict[str, str] = {}
    model = _Meddler(lambda: _owner(db, CHANGES[change], given))
    world = _World(db, fake, model=model)
    person = world.person()
    document = _filed(world, person, "bill.pdf", "water bill")
    _owner(db, "UPDATE documents SET summary = 'A water bill.' WHERE id = :d", {"d": document})
    given.update(d=document, other=world.api.add_user(person.c, "member")[0])
    world.tap(person, "get.mr")
    assert world.say(person, "water") == (say("not_found", "mr"), ["menu.mr"])
    assert model.asked == 1 and _links(world, person) == []
    assert "bill.pdf" not in repr(world.fake.requests)


@pytest.mark.parametrize("change", list(CHANGES))
def test_an_answer_whose_source_goes_while_it_is_made_is_not_sent(
    db: Database, fake: WhatsAppFake, change: str
) -> None:
    given: dict[str, str] = {}
    world = _World(db, fake, model=_Meddler(lambda: _owner(db, CHANGES[change], given)))
    person = world.person()
    document = world.api.add_document(person.c, name="bill 1.pdf", uploaded_by=person.u)
    given.update(d=document, other=world.api.add_user(person.c, "member")[0])
    world.api.answers.reply = _cited_answer(document)
    assert world.say(person, "एकूण रक्कम किती आहे?") == (say("not_found", "mr"), ["menu.mr"])
    assert "bill 1.pdf" not in repr(world.fake.requests)


def test_a_choice_locked_while_the_question_is_translated_is_left_out(
    db: Database, fake: WhatsAppFake
) -> None:
    given: dict[str, str] = {}
    world = _World(db, fake, model=_Meddler(lambda: _owner(db, CHANGES["locked"], given)))
    person = world.person()
    pune, panvel = (world.api.add_document(person.c, uploaded_by=person.u) for _ in range(2))
    given.update(d=pune, other=world.api.add_user(person.c, "member")[0])
    world.api.answers.reply = _clarify(pune, panvel)
    _, ids = world.say(person, "पुण्यातील जमीन?")
    assert [i.split(":")[2] for i in ids] == ["c2", "other"]  # Pune's file locked: its choice is gone


def test_a_connection_ended_while_the_reply_is_made_gets_not_connected(
    db: Database, fake: WhatsAppFake
) -> None:
    given: dict[str, str] = {}
    bump = "UPDATE users SET session_version = session_version + 1 WHERE id = :u"
    world = _World(db, fake, model=_Meddler(lambda: _owner(db, bump, given)))
    person = world.person()
    given.update(u=person.u)
    world.api.answers.reply = answer_json()
    assert world.say(person, "एकूण रक्कम किती आहे?") == NOT_CONNECTED  # its password changed, say
    ended = _owner(db, "SELECT end_reason FROM whatsapp_links WHERE id = :l", {"l": person.link})
    assert ended == [("session",)]


def test_a_reply_tried_again_is_made_once_and_its_card_audited_once(db: Database, fake: WhatsAppFake) -> None:
    translator = _Translator()
    world = _World(db, fake, model=translator)
    person = world.person()
    document = _filed(world, person, "bill.pdf", "water bill")
    _owner(db, "UPDATE documents SET summary = 'The total is 1,200.' WHERE id = :d", {"d": document})
    world.tap(person, "get.mr")
    world.fake.failures = [500]
    assert post(world.client, delivery(message(person.number, world.api.clock(), "water"))).status_code == 200
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    assert _links(world, person) == []  # not sent: not audited
    _run_again(world, person)
    assert world.replies(person)[-1][0].startswith("bill.pdf\n")
    searched = [row for row in world.api.audit_rows(person.c) if row[0] == "search.run"]
    assert translator.asked == 1 and len(searched) == 1 and len(_links(world, person)) == 1


def test_a_card_refused_for_good_is_never_audited(world: _World) -> None:
    person = world.person()
    _filed(world, person, "bill.pdf", "water bill")
    world.tap(person, "get.en")
    world.fake.failures = [400]  # Meta refuses it for good: the message fails
    assert post(world.client, delivery(message(person.number, world.api.clock(), "water"))).status_code == 200
    for claim in world.channel.claim(100):
        world.channel.handle(claim)
    assert _links(world, person) == [] and world.channel.made == {}


# --- review of W4 (#603): the fallbacks, limits and words ---------------------------------------


def test_a_lock_grant_holder_gets_no_assumption_or_total(world: _World) -> None:
    """shortcut: until PR 12 stops Ask reading locked files. A name only a locked file holds may be
    in what Ask assumed, or in a fixed query's total, neither citing the file."""
    person = world.person()
    _lock(world, person, _filed(world, person, "Zebra Patil land.pdf", "survey"))
    said = {**answer_json(), "assumptions": ["I took Zebr as Zebra Patil."]}
    world.api.answers.reply = said
    body, _ = world.say(person, "Land of Zebr?")
    assert body.startswith("The total is 1,200.") and "Zebra" not in body
    counted = {**answer_json(), "route": "fixed_query"}
    world.api.answers.reply = {
        **counted,
        "sentences": [{"text": "2 documents name Zebra Patil.", "citations": []}],
    }
    assert world.say(person, "How many documents mention Zebra?") == (NOT_FOUND, ["another.en", "menu.en"])
    other = world.person(person.c)  # no grant: the control
    world.api.answers.reply = said
    assert world.say(other, "Land of Zebr?")[0].startswith("I took Zebr as Zebra Patil.\nThe total")


def test_a_typed_reply_to_a_did_you_mean_gone_since_is_a_new_question(world: _World) -> None:
    person = world.person()
    document = world.api.add_document(person.c, uploaded_by=person.u)
    _clarified(world, person, document)
    _owner(world.db, "DELETE FROM documents WHERE id = :d", {"d": document})  # Ask: CLARIFY_GONE
    assert world.say(person, "Pune")[0].startswith("The total is 1,200.")
    assert world.api.answers.resumed == [] and world.api.answers.asked[-1] == (person.c, "Pune")


def test_a_conversation_deleted_meanwhile_is_asked_in_a_new_one(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = world.person()
    gone = Current(str(uuid.uuid4()), str(uuid.uuid4()), "assistant", "answered", True)
    monkeypatch.setattr(wa_chat, "current", lambda *args: gone)  # Ask: 404
    assert world.say(person, "What is the total?")[0].startswith("The total is 1,200.")
    assert len(person.signed.client.get("/api/conversations").json()["items"]) == 1


def test_searches_and_cards_count_against_asks_limit(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake, chat_questions_per_minute=2)
    person = world.person()
    document = _filed(world, person, "bill.pdf", "water bill")
    world.say(person, "What is the total?")
    world.tap(person, "get.en")
    assert world.say(person, "water")[0].startswith("bill.pdf\n")
    assert world.tap(person, f"file:{document}:en", row=True) == say("too_many", "en")


def test_two_choices_alike_in_their_first_20_letters_are_a_list(world: _World) -> None:
    person = world.person()
    files = [world.api.add_document(person.c, uploaded_by=person.u) for _ in range(2)]
    reply = _clarify(*files)
    choices = reply["clarify"]["choices"]
    choices[0]["label"], choices[1]["label"] = "Ramesh Patil Junior One", "Ramesh Patil Junior Two"
    world.api.answers.reply = reply
    world.say(person, "Land of Ramesh?")
    raw = world.raw(person)["interactive"]
    assert raw["type"] == "list"
    assert [r["title"] for r in raw["action"]["sections"][0]["rows"][:2]] == [
        "Ramesh Patil Junior One",
        "Ramesh Patil Junior Two",
    ]


def test_a_model_not_allowed_is_said_so(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    person = world.person()

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise ApiError(403, "model_not_allowed", "x")

    monkeypatch.setattr(chat_router, "ask", refused)
    assert world.say(person, "What is the total?") == say("model_not_allowed", "en")


def test_a_person_who_must_set_a_new_password_gets_only_that(world: _World) -> None:
    person = world.person()
    _owner(world.db, "UPDATE users SET must_change_password = true WHERE id = :u", {"u": person.u})
    assert world.say(person, "What is the total?") == say("change_password", "en")
    assert world.tap(person, "get.en") == say("change_password", "en")
    assert world.api.answers.asked == []


def test_a_sticker_after_which_file_is_answered_in_its_language(world: _World) -> None:
    person = world.person()
    world.tap(person, "get.mr")
    # Changed on purpose (#603 W5, W6): a photo is a file to save now, a voice note a question; a
    # sticker is still typed only.
    sticker = {**message(person.number, world.api.clock()), "type": "sticker", "sticker": {"id": "1"}}
    assert world.send(person, sticker) == say("typed_only", "mr")


def test_a_number_is_masked_before_it_is_translated(db: Database, fake: WhatsAppFake) -> None:
    model = _Meddler(lambda: None)
    world = _World(db, fake, model=model)
    person = world.person()
    sentence = {"text": "The account number is 001234567890.", "citations": []}
    world.api.answers.reply = {**answer_json(), "sentences": [sentence]}
    body, _ = world.say(person, "खाते क्रमांक काय आहे?")
    assert model.asked == 1 and "001234567890" not in "".join(model.seen)
    assert "••••7890" in body and say("masked", "mr") in body
