"""Kaagaz on WhatsApp's voice notes (#603 W6, PRD 7 section 15 "Voice notes", decision 10), end to
end on the fake WhatsApp: a signed voice note through the webhook, its audio from the fake's media,
a fake model writing it down, the channel in the API's role, the browser's Ask behind it.

A voice note is asked as the same words typed, the reply starting "I heard: ..." (masked), in the
language spoken (Hindi and Marathi too), as text; in "Which file?" it names the file; room is kept
so a cut never takes a link. Refused in plain words: forwarded, voice notes switched off (and then
[Ask a question] does not offer them), over a minute or too big (Meta's size before anything is
read, then the bytes), not a voice note, Meta or the model down or off, Ask off, Ask's limit. An
unclear note asks again; an unsure one asks "Is that right?" first. The audio is never kept, the
words heard are never logged, and the note is billed to the person as ``transcribe``. A voice note
never disconnects."""

import base64
import logging
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.ai import usage
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.api.routers import chat as chat_router
from kaagaz.channel import chat as chat_module
from kaagaz.channel import voice as voice_module
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.texts import MASK, MAX_BODY, say
from kaagaz.db.engine import Database
from tests.test_api_chat import _cited_answer
from tests.test_api_support import Api, Signed
from tests.test_operator_auth_db import _owner
from tests.test_search_db import add_pieces
from tests.whatsapp_fake import (
    APP_URL,
    NUMBER_KEY,
    WhatsAppFake,
    connect,
    delivery,
    file_message,
    message,
    new_number,
    ogg_opus,
    post,
    running_fake,
    voice_message,
)
from tests.whatsapp_fake import whatsapp_settings as settings_for

pytestmark = pytest.mark.db

QUESTION = "What is the electricity bill for March?"
HINDI = "मार्च का बिजली का बिल कितना है?"
MARATHI = "मार्च महिन्याचे वीज बिल किती आहे?"


class Ears:
    """The model: each voice note written down as ``heard`` (or ``down`` raised), billed as the real
    client bills a reply; a Hindi or Marathi "translation" is ``translated``."""

    def __init__(
        self,
        text: str = QUESTION,
        lang: str = "en",
        *,
        sure: bool = True,
        down: Exception | None = None,
        translated: str = "",
    ) -> None:
        self.heard = {"text": text, "language": lang, "sure": sure}
        self.down, self.translated = down, translated
        self.audio: list[bytes] = []  # every note it was given
        self.systems: list[str] = []  # and its instructions

    def transcribe(
        self,
        *,
        system: str,
        audio: bytes,
        audio_format: str,
        max_tokens: int,
        deadline: float | None,
        **_: Any,
    ) -> dict[str, Any]:
        assert audio_format == "ogg" and "untrusted" in system and max_tokens == voice_module.MAX_HEARD_TOKENS
        assert deadline is not None and deadline <= time.monotonic() + voice_module.TRANSCRIBE_SECONDS
        self.systems.append(system)
        self.audio.append(audio)
        if self.down is not None:
            raise self.down
        usage.record_call("google/gemini-3.8-flash", "Google", 900, 30, 0, 0.0007)
        return dict(self.heard)

    def chat_json(self, *, system: str, user: str, **_: Any) -> dict[str, Any]:
        return {"text": self.translated}

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("no embedding here")


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


def _or_ears(model: Any) -> Any:
    """``model``, else ``Ears`` (Api takes a ModelClient: the fakes stand in for one)."""
    return Ears() if model is None else model


class World:
    def __init__(self, db: Database, fake: WhatsAppFake, *, model: Any = None, **settings: Any) -> None:
        self.db, self.fake = db, fake
        self.ears = _or_ears(model)
        self.api = Api(db, settings=settings_for(fake, **settings), model=self.ears)
        self.client = self.api.client()
        self.channel = Channel(
            db=self.api.app_db,
            sender=MetaWhatsApp.from_settings(self.api.settings),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
            chat=Chat(self.api.app.state.kaagaz),
        )

    def person(self, customer: str | None = None) -> Person:
        if customer is None:
            customer, _ = self.api.customer()
        signed = self.api.sign_in(customer, "member")
        number = new_number()
        return Person(signed, number, connect(self.db, customer, signed.user_id, number))

    def send(self, person: Person, item: dict[str, Any]) -> Any:
        assert post(self.client, delivery(item)).status_code == 200
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)
        return [what for to, what in self.fake.sent() if to == person.number.removeprefix("+")][-1]

    def speak(self, person: Person, audio: bytes | None = None, *, forwarded: bool = False) -> Any:
        """A voice note (``audio``: 5 seconds of one, unless given) sent and answered."""
        media = self.fake.add_media(ogg_opus(5) if audio is None else audio)
        return self.send(person, voice_message(person.number, self.api.clock(), media, forwarded=forwarded))

    def say(self, person: Person, words: str) -> Any:
        return self.send(person, message(person.number, self.api.clock(), words))

    def tap(self, person: Person, tapped: str) -> Any:
        item = {
            **message(person.number, self.api.clock()),
            "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": tapped, "title": "x"}},
        }
        return self.send(person, item)


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


@pytest.fixture
def world(db: Database, fake: WhatsAppFake) -> World:
    return World(db, fake)


def _voice(db: Database, person: Person, on: bool) -> None:
    _owner(
        db, "UPDATE whatsapp_org_settings SET voice = :on WHERE customer_id = :c", {"on": on, "c": person.c}
    )


# --- asked as the words typed ------------------------------------------------------------------


def test_a_voice_note_is_asked_as_the_words_typed_and_the_reply_starts_i_heard(world: World) -> None:
    person = world.person()
    document = world.api.add_document(person.c, name="bill 1.pdf", uploaded_by=person.u)
    world.api.answers.reply = _cited_answer(document)
    body, buttons = world.speak(person)
    link = f"{APP_URL}/documents/{document}?page=1"
    assert (
        body
        == f'I heard: "{QUESTION}"\n\nSentence 1. All of them.\n\nSource: bill 1.pdf, page 1\nOpen: {link}'
    )
    assert buttons == ["another.en", "menu.en"]
    assert world.api.answers.asked == [(person.c, QUESTION)]  # the browser's Ask, the words as heard
    assert [body["type"] for _, _, body in world.fake.requests] == ["interactive"]  # text, never voice
    listed = person.signed.client.get("/api/conversations").json()["items"]
    assert [item["channel"] for item in listed] == ["whatsapp"]


@pytest.mark.parametrize(
    ("lang", "spoken", "translated", "heard"),
    [("hi", HINDI, "वाक्य 1. ये सभी।", "मैंने सुना"), ("mr", MARATHI, "वाक्य 1. हे सर्व.", "मी ऐकले")],
)
def test_hindi_and_marathi_are_answered_in_the_language_spoken(
    db: Database, fake: WhatsAppFake, lang: str, spoken: str, translated: str, heard: str
) -> None:
    world = World(db, fake, model=Ears(spoken, lang, translated=translated))
    person = world.person()
    document = world.api.add_document(person.c, name="bill 1.pdf", uploaded_by=person.u)
    world.api.answers.reply = _cited_answer(document)
    body, buttons = world.speak(person)
    assert body.startswith(f'{heard}: "{spoken}"\n\n{translated}\n\n')
    assert buttons == [f"another.{lang}", f"menu.{lang}"]
    assert world.api.answers.asked == [(person.c, spoken)]


def test_in_which_file_a_voice_note_names_the_file(world: World) -> None:
    person = world.person()
    world.ears.heard["text"] = "electricity"
    document = world.api.add_document(person.c, name="MSEB March.pdf", uploaded_by=person.u)
    add_pieces(world.db, person.c, document, [("electricity bill march", None)])
    assert world.tap(person, "get.en") == say("which_file_voice", "en")
    body, _ = world.speak(person)
    assert body.startswith('I heard: "electricity"\n\nMSEB March.pdf\n')
    assert body.endswith(f"Open in Kaagaz: {APP_URL}/documents/{document}")
    assert world.api.answers.asked == []  # a file's name, not a question


def test_room_is_kept_for_i_heard_so_no_link_is_cut(world: World) -> None:
    person = world.person()
    world.ears.heard["text"] = "word " * 100
    document = world.api.add_document(person.c, name="bill 1.pdf", uploaded_by=person.u)
    world.api.answers.reply = {
        **_cited_answer(document),
        "sentences": [{"text": "long " * 400, "citations": [1]}],
    }
    body, _ = world.speak(person)
    conversation = person.signed.client.get("/api/conversations").json()["items"][0]["id"]
    assert len(body) <= MAX_BODY
    assert f"Full answer in Kaagaz: {APP_URL}/chat?c={conversation}" in body
    assert body.endswith(f"Open: {APP_URL}/documents/{document}?page=1")
    # A card too.
    folder = world.api.add_folder(person.c, person.u, "f" * 100)
    card = world.api.add_document(person.c, name="w" * 200 + ".pdf", uploaded_by=person.u, folder_id=folder)
    add_pieces(world.db, person.c, card, [("water bill", None)])
    _owner(world.db, "UPDATE documents SET summary = :s WHERE id = :d", {"s": "summary " * 60, "d": card})
    world.ears.heard["text"] = "water " * 60
    world.tap(person, "get.en")
    body, _ = world.speak(person)
    assert len(body) <= MAX_BODY and body.endswith(f"Open in Kaagaz: {APP_URL}/documents/{card}")


def test_id_numbers_are_masked_in_i_heard(world: World) -> None:
    person = world.person()
    world.ears.heard["text"] = (
        "Is account 001234567890 or PAN ABCDE1234F or Aadhaar 1234 5678 9012 in my files?"
    )
    world.api.answers.reply = {**_cited_answer(), "sentences": [{"text": "No.", "citations": []}]}
    body, _ = world.speak(person)
    assert body.startswith(
        f'I heard: "Is account {MASK}7890 or PAN {MASK}234F or Aadhaar {MASK}9012 in my files?"'
    )
    sent = repr(world.fake.requests)
    assert not any(full in sent for full in ("001234567890", "ABCDE1234F", "1234 5678 9012"))
    assert world.api.answers.asked == [(person.c, world.ears.heard["text"])]  # Ask gets the words whole


def test_a_spoken_stop_never_disconnects(world: World) -> None:
    person = world.person()
    world.ears.heard["text"] = "STOP"
    world.speak(person)
    assert world.api.answers.asked == [(person.c, "STOP")]
    world.api.answers.asked.clear()
    world.say(person, "What is the total?")
    assert world.api.answers.asked == [(person.c, "What is the total?")]  # still connected


# --- refused in plain words --------------------------------------------------------------------


def test_voice_notes_switched_off_are_refused_and_not_offered(world: World) -> None:
    person = world.person()
    assert world.tap(person, "ask.en") == say("ask_voice", "en")
    _voice(world.db, person, False)
    assert world.speak(person) == say("voice_off", "en")
    assert world.tap(person, "get.en") == say("which_file", "en")
    assert world.tap(person, "ask.mr") == say("ask", "mr")
    assert world.fake.fetched == [] and world.ears.audio == [] and world.api.answers.asked == []
    _voice(world.db, person, True)
    world.speak(person)
    assert world.api.answers.asked == [(person.c, QUESTION)]


def test_a_forwarded_voice_note_is_refused(world: World) -> None:
    person = world.person()
    assert world.speak(person, forwarded=True) == say("voice_forwarded", "en")
    assert world.fake.fetched == [] and world.ears.audio == [] and world.api.answers.asked == []


@pytest.mark.parametrize("seconds", [61, 600])
def test_a_voice_note_over_a_minute_is_refused(world: World, seconds: int) -> None:
    person = world.person()
    assert world.speak(person, ogg_opus(seconds)) == say("voice_limit", "en")
    assert world.ears.audio == [] and world.api.answers.asked == []
    world.speak(person, ogg_opus(60.5))  # a note WhatsApp shows as 1:00
    assert world.api.answers.asked == [(person.c, QUESTION)]


def test_a_voice_note_too_big_is_refused_by_metas_size_before_it_is_read(world: World) -> None:
    person = world.person()
    media = world.fake.add_media(ogg_opus(5))
    world.fake.sizes[media] = voice_module.MAX_VOICE_BYTES + 1
    assert world.send(person, voice_message(person.number, world.api.clock(), media)) == say(
        "voice_limit", "en"
    )
    assert world.fake.fetched == [] and world.ears.audio == []


def test_a_voice_note_too_big_is_refused_by_its_bytes(world: World) -> None:
    person = world.person()
    big = ogg_opus(5, pad=voice_module.MAX_VOICE_BYTES + 1000)
    media = world.fake.add_media(big)
    world.fake.sizes[media] = 1000  # Meta says less
    assert world.send(person, voice_message(person.number, world.api.clock(), media)) == say(
        "voice_limit", "en"
    )
    assert world.fake.fetched == [media] and world.ears.audio == []


def test_audio_that_is_not_a_voice_note_is_refused(world: World) -> None:
    person = world.person()
    assert world.speak(person, b"ID3\x04" + b"\0" * 4000) == say("voice_limit", "en")
    assert world.ears.audio == []


@pytest.mark.parametrize(
    "down",
    [ModelUnavailable("timeout"), ModelUnavailable("circuit_open"), ModelOutputError("invalid_output")],
)
def test_the_model_down_gets_a_plain_reply(db: Database, fake: WhatsAppFake, down: Exception) -> None:
    world = World(db, fake, model=Ears(down=down))
    person = world.person()
    assert world.speak(person) == say("voice_failed", "en")
    assert len(world.ears.audio) == 1 and world.api.answers.asked == []


class _Deaf:
    """A model client that cannot write a voice note down."""

    def chat_json(self, *, system: str, user: str, **_: Any) -> dict[str, Any]:
        raise AssertionError("no translation here")

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("no embedding here")


def test_meta_down_or_no_model_to_hear_it_gets_a_plain_reply(db: Database, fake: WhatsAppFake) -> None:
    world = World(db, fake)
    person = world.person()
    for broken in (world.fake.gone, world.fake.broken):  # Meta has it no more; Meta down
        media = world.fake.add_media(ogg_opus(5))
        broken.add(media)
        sent = voice_message(person.number, world.api.clock(), media)
        assert world.send(person, sent) == say("voice_failed", "en")
    assert world.ears.audio == []
    deaf = World(db, fake, model=_Deaf())
    other = deaf.person()
    assert deaf.speak(other) == say("voice_failed", "en")
    assert world.api.answers.asked == deaf.api.answers.asked == [] and fake.fetched == []


def test_asks_limit_comes_before_the_note_is_written_down(db: Database, fake: WhatsAppFake) -> None:
    world = World(db, fake, chat_questions_per_minute=2)
    person = world.person()
    world.speak(person)  # its hearing and its question: Ask's limit counts both, as a search and a card
    assert world.speak(person) == say("too_many", "en")
    assert len(world.ears.audio) == 1


def test_ask_off_refuses_a_question_but_a_file_named_by_voice_still_comes(world: World) -> None:
    """Ask's switch is Ask's: a note naming a file after [Get a file] is not a question."""
    person = world.person()
    _owner(
        world.db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, 'ask', false)",
        {"c": person.c},
    )
    assert world.speak(person) == f'I heard: "{QUESTION}"\n\n' + say("ask_off", "en")
    world.ears.heard["text"] = "electricity"
    document = world.api.add_document(person.c, name="MSEB March.pdf", uploaded_by=person.u)
    add_pieces(world.db, person.c, document, [("electricity bill march", None)])
    world.tap(person, "get.en")
    body, _ = world.speak(person)
    assert body.startswith('I heard: "electricity"\n\nMSEB March.pdf\n')
    assert world.api.answers.asked == []


# --- unclear and unsure ------------------------------------------------------------------------


def test_a_note_with_no_words_asks_again(world: World) -> None:
    person = world.person()
    world.ears.heard.update(text="", language="mr")
    assert world.speak(person) == say("voice_unclear", "mr")
    assert world.api.answers.asked == []


def test_an_unsure_note_is_confirmed_before_anything_is_searched(world: World) -> None:
    person = world.person()
    world.ears.heard["sure"] = False
    body, (yes, no) = world.speak(person)
    assert body == say("heard_sure", "en", QUESTION)
    assert yes.startswith("heard:yes:") and no.startswith("heard:no:") and yes.endswith(":en")
    assert world.api.answers.asked == []
    assert world.tap(person, no) == say("say_again", "en")
    assert world.api.answers.asked == []
    assert world.tap(person, yes) == (say("choice_gone", "en"), ["another.en", "menu.en"])  # gone
    _, (again, _) = world.speak(person)
    body, _ = world.tap(person, again)
    assert body.startswith(f'I heard: "{QUESTION}"\n\n')
    assert world.api.answers.asked == [(person.c, QUESTION)]
    assert world.tap(person, again)[0] == say("choice_gone", "en")  # once


def test_an_older_cards_yes_never_answers_a_newer_note(world: World) -> None:
    person = world.person()
    world.ears.heard.update(text="the older words", sure=False)
    _, (older, _) = world.speak(person)
    world.ears.heard["text"] = QUESTION
    _, (newer, _) = world.speak(person)
    assert older != newer
    assert world.tap(person, older)[0] == say("choice_gone", "en")
    assert world.tap(person, older.replace(older.split(":")[2], "made-up"))[0] == say("choice_gone", "en")
    assert world.api.answers.asked == []
    world.tap(person, newer)
    assert world.api.answers.asked == [(person.c, QUESTION)]


def test_an_unsure_note_in_which_file_still_names_the_file(world: World) -> None:
    person = world.person()
    world.ears.heard.update(text="electricity", sure=False)
    document = world.api.add_document(person.c, name="MSEB March.pdf", uploaded_by=person.u)
    add_pieces(world.db, person.c, document, [("electricity bill march", None)])
    world.tap(person, "get.en")
    _, (yes, _) = world.speak(person)
    body, _ = world.tap(person, yes)
    assert body.startswith('I heard: "electricity"\n\nMSEB March.pdf\n')
    assert world.api.answers.asked == []


def test_a_number_dictated_in_groups_is_one_number_and_masked(world: World) -> None:
    person = world.person()
    world.ears.heard["text"] = "Is Aadhaar 1234, 5678, 9012 in my files?"
    world.api.answers.reply = {**_cited_answer(), "sentences": [{"text": "No.", "citations": []}]}
    body, _ = world.speak(person)
    assert body.startswith(f'I heard: "Is Aadhaar {MASK}9012 in my files?"')
    assert "one unbroken number" in world.ears.systems[0]  # what the model is asked to write
    assert "5678" not in repr([body for _, _, body in world.fake.requests])  # the text sent, not the URL


def test_an_unsure_note_in_which_file_then_the_name_typed_still_names_the_file(world: World) -> None:
    """Any other message puts back the step an unsure note hid ("Which file?" here)."""
    person = world.person()
    world.ears.heard.update(text="something unclear", sure=False)
    document = world.api.add_document(person.c, name="MSEB March.pdf", uploaded_by=person.u)
    add_pieces(world.db, person.c, document, [("electricity bill march", None)])
    world.tap(person, "get.en")
    _, (yes, _) = world.speak(person)
    body, _ = world.say(person, "electricity")
    assert body.startswith("MSEB March.pdf\n")
    assert world.tap(person, yes)[0] == say("choice_gone", "en")  # stale now
    assert world.api.answers.asked == []


def _waiting_files(world: World, person: Person) -> list[str]:
    rows = _owner(
        world.db,
        "SELECT state FROM whatsapp_pickers WHERE customer_id = :c AND user_id = :u",
        {"c": person.c, "u": person.u},
    )
    return [each["m"] for each in (rows[0][0].get("files") or [])] if rows else []


def test_an_unsure_note_while_files_wait_keeps_every_file_waiting(world: World) -> None:
    person = world.person()
    _owner(
        world.db, "UPDATE whatsapp_org_settings SET uploads = true WHERE customer_id = :c", {"c": person.c}
    )
    sent = world.api.clock() - timedelta(seconds=20)  # answered at once (W5's QUIET_SECONDS)
    files = [world.fake.add_media(b"%PDF-1.7 a"), world.fake.add_media(b"x")]
    world.send(person, file_message(person.number, sent, files[0], filename="a.pdf"))
    first = _waiting_files(world, person)
    assert len(first) == 1
    world.ears.heard["sure"] = False
    _, (yes, _) = world.speak(person)
    world.send(person, file_message(person.number, sent, files[1], kind="image"))
    waiting = _waiting_files(world, person)
    assert len(waiting) == 2 and waiting[0] == first[0]
    assert world.tap(person, yes)[0] == say("choice_gone", "en")  # stale: nothing asked, nothing lost
    assert _waiting_files(world, person) == waiting and world.api.answers.asked == []
    assert not set(files) & set(world.fake.fetched)  # no file fetched before a tap (the note was)


def test_an_unsure_note_is_masked_too(world: World) -> None:
    person = world.person()
    world.ears.heard.update(text="account 001234567890", sure=False)
    assert world.speak(person)[0] == say("heard_sure", "en", f"account {MASK}7890")


# --- privacy and billing -----------------------------------------------------------------------


def _whatsapp_rows(db: Database, person: Person) -> str:
    rows = _owner(
        db,
        "SELECT m.*, p.state FROM whatsapp_messages m LEFT JOIN whatsapp_pickers p"
        " ON p.customer_id = m.customer_id AND p.user_id = m.user_id WHERE m.customer_id = :c",
        {"c": person.c},
    )
    return repr(rows)


def test_the_audio_is_never_kept(world: World) -> None:
    person = world.person()
    audio = ogg_opus(5, pad=3000)
    before = set(os.listdir(tempfile.gettempdir()))
    world.speak(person, audio)
    assert world.ears.audio == [audio]  # it was heard, from Meta's media
    assert set(os.listdir(tempfile.gettempdir())) <= before  # never written to disk
    stored = world.api.storage
    assert not any(audio in blob for blob in [*stored._blobs.values(), *stored.quarantined.values()])
    kept = _whatsapp_rows(world.db, person)
    assert base64.b64encode(audio[-200:]).decode() not in kept and repr(audio[-200:]) not in kept
    assert QUESTION not in kept  # nor the words heard, once answered (Ask's history keeps the question)


def test_the_words_heard_are_never_logged(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    person = world.person()
    world.ears.heard["text"] = "secret electricity question 4821"
    world.speak(person)
    world.ears.heard["sure"] = False
    _, (yes, _) = world.speak(person)
    world.tap(person, yes)
    world.ears.down = ModelUnavailable("timeout")
    world.speak(person)
    assert len(world.api.answers.asked) == 2
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "secret electricity" not in logged


def test_a_voice_note_is_billed_to_the_person_as_transcribe(world: World) -> None:
    person = world.person()
    world.speak(person)
    rows = _owner(
        world.db,
        "SELECT kind, user_id, calls, input_tokens FROM model_usage WHERE customer_id = :c ORDER BY kind",
        {"c": person.c},
    )
    assert [(row[0], str(row[1]), row[2], row[3]) for row in rows] == [("transcribe", person.u, 1, 900)]


def test_the_lease_is_renewed_once_the_note_is_heard(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetching and hearing a note take their time before Ask takes its own: the lease is renewed
    between them (W5's ``keep_lease``)."""
    renewed: list[int] = []
    monkeypatch.setattr(chat_module, "keep_lease", lambda: renewed.append(len(world.ears.audio)))
    person = world.person()
    world.speak(person)
    assert renewed == [1] and world.api.answers.asked == [(person.c, QUESTION)]


def _usage_rows(world: World, person: Person) -> list[tuple[str, int]]:
    rows = _owner(
        world.db, "SELECT kind, calls FROM model_usage WHERE customer_id = :c ORDER BY kind", {"c": person.c}
    )
    return [(row[0], row[1]) for row in rows]


def _run_again(world: World, person: Person) -> Any:
    """The connection's rest (a reply not sent, or an error) over: handled again; the last reply."""
    _owner(world.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE id = :l", {"l": person.link})
    while claimed := world.channel.claim(100):
        for claim in claimed:
            world.channel.handle(claim)
    return [what for to, what in world.fake.sent() if to == person.number.removeprefix("+")][-1]


def test_a_reply_not_sent_once_is_sent_later_with_the_note_heard_once(world: World) -> None:
    person = world.person()
    world.fake.failures = [500]  # Meta refuses the reply once
    media = world.fake.add_media(ogg_opus(5))
    assert (
        post(world.client, delivery(voice_message(person.number, world.api.clock(), media))).status_code
        == 200
    )
    while claimed := world.channel.claim(100):
        for claim in claimed:
            world.channel.handle(claim)
    body, _ = _run_again(world, person)
    assert body.startswith(f'I heard: "{QUESTION}"')
    assert len(world.ears.audio) == 1 and world.fake.fetched == [media]
    assert _usage_rows(world, person) == [("transcribe", 1)]


def test_a_note_whose_ask_fails_once_is_heard_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ask (or the database) fails while the reply is made: the message is tried again, and its note
    is never fetched, sent or billed again (``Voice`` keeps what it heard by message id)."""
    person = world.person()
    real, failed = chat_router.ask, list[bool]()

    def ask_once(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise RuntimeError("the database went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(chat_router, "ask", ask_once)
    media = world.fake.add_media(ogg_opus(5))
    with pytest.raises(RuntimeError):
        world.send(person, voice_message(person.number, world.api.clock(), media))
    body, _ = _run_again(world, person)
    assert body.startswith(f'I heard: "{QUESTION}"') and failed == [True]
    assert len(world.ears.audio) == 1 and world.fake.fetched == [media]
    assert _usage_rows(world, person) == [("transcribe", 1)]
