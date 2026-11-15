"""Kaagaz on WhatsApp's uploads (#603 W5, PRD 7 section 15 "Upload a file", "Choosing a folder"),
end to end on the fake WhatsApp: a signed message through the webhook, the channel in the API's role,
the browser's own upload behind it.

The menu's three buttons and the typed More and Help; the typing indicator and "Looking through your
files…"; a document saved into a folder the person picks (the same document row a browser upload
makes, audited ``via: whatsapp`` with the person as subject, the file never sent back), a recent
folder; the list's pages, a folder's buttons, Open folder and Back, a typed folder name, Let Kaagaz
decide; files sent one after another answered together, photos as one document or separate files;
Cancel, an expired wait, uploads switched off, the hourly limit; too large (by Meta's size, before it
is fetched, and by the upload's own check), the wrong type, empty, gone at Meta; duplicates as the
browser answers them; the "ready", virus-check and "couldn't read" notices, and none for a file
hidden since; names masked."""

import hashlib
import io
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pypdfium2 as pdfium
import pytest
from PIL import Image
from sqlalchemy import text

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.api.errors import ApiError
from kaagaz.channel import upload as upload_module
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.outgoing import Outgoing
from kaagaz.channel.texts import say
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.whatsapp import Linked, Waiting
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import JobFailed
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.db_support import insert_folder
from tests.fakes import FakeScanner
from tests.test_api_support import CSRF, Api, Signed
from tests.test_operator_auth_db import _owner
from tests.whatsapp_fake import (
    APP_URL,
    NUMBER_KEY,
    WhatsAppFake,
    connect,
    delivery,
    file_message,
    message,
    new_number,
    post,
    running_fake,
)
from tests.whatsapp_fake import whatsapp_settings as settings_for

pytestmark = pytest.mark.db

PATH = " " + chr(0x203A) + " "  # between the folders of a path


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def pdf(tag: str) -> bytes:
    return b"%PDF-1.7\n% " + tag.encode() + b"\n" + b"0" * 200


def jpeg(width: int = 60, height: int = 40) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (width % 255, 10, 20)).save(out, "JPEG")
    return out.getvalue()


def tap(number: str, sent_at: Any, tapped: str) -> dict[str, Any]:
    return {
        "from": number.removeprefix("+"),
        "id": f"wamid.{uuid.uuid4().hex}",
        "timestamp": str(int(sent_at.timestamp())),
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": tapped, "title": "x"}},
    }


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


class World:
    def __init__(self, db: Database, fake: WhatsAppFake, **settings: Any) -> None:
        self.db, self.fake = db, fake
        self.api = Api(db, settings=settings_for(fake, **settings))
        self.client = self.api.client()
        self.chat = Chat(self.api.app.state.kaagaz)
        self.channel = Channel(
            db=self.api.app_db,
            sender=MetaWhatsApp.from_settings(self.api.settings),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
            chat=self.chat,
        )

    def person(self, customer: str | None = None, *, uploads: bool = True) -> Person:
        if customer is None:
            customer, _ = self.api.customer()
        signed = self.api.sign_in(customer, "member")
        number = new_number()
        person = Person(signed, number, connect(self.db, customer, signed.user_id, number))
        _owner(
            self.db,
            "UPDATE whatsapp_org_settings SET uploads = :on WHERE customer_id = :c",
            {"on": uploads, "c": customer},
        )
        return person

    def folder(self, person: Person, name: str, parent: str | None = None) -> str:
        with self.db.customer(person.c) as conn:
            return insert_folder(conn, person.c, person.u, name, parent)

    def run(self) -> None:
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)

    def send(self, person: Person, *messages: dict[str, Any]) -> Any:
        """``messages`` posted and handled, as if a notice's rest (NOTICE_SECONDS) had passed; the
        last reply to ``person`` (as ``fake.sent`` shows it)."""
        assert post(self.client, delivery(*messages)).status_code == 200
        self.free(person)
        self.run()
        return self.replies(person)[-1]

    def file(
        self, person: Person, data: bytes, name: str | None = "bill.pdf", *, kind: str = "document"
    ) -> Any:
        """A file sent QUIET_SECONDS ago and more (so it is answered at once)."""
        media = self.fake.add_media(data)
        sent = self.api.clock() - timedelta(seconds=20)
        return self.send(person, file_message(person.number, sent, media, filename=name, kind=kind))

    def say(self, person: Person, words: str) -> Any:
        return self.send(person, message(person.number, self.api.clock(), words))

    def tap(self, person: Person, tapped: str) -> Any:
        return self.send(person, tap(person.number, self.api.clock(), tapped))

    def replies(self, person: Person) -> list[Any]:
        return [what for to, what in self.fake.sent() if to == person.number.removeprefix("+")]

    def documents(self, person: Person) -> list[dict[str, Any]]:
        rows = _owner(
            self.db,
            "SELECT id, display_name, folder_id, batch_id, uploaded_by, status FROM documents"
            " WHERE customer_id = :c AND uploaded_by = :u ORDER BY created_at, id",
            {"c": person.c, "u": person.u},
        )
        keys = ("id", "name", "folder", "batch", "by", "status")
        return [dict(zip(keys, [None if v is None else str(v) for v in row], strict=True)) for row in rows]

    def free(self, person: Person) -> None:
        """The connection's lease freed (a rest for files still coming or a reply to try again), and
        its notices put aside brought back as their time came (``Channel.revive``)."""
        _owner(self.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE id = :l", {"l": person.link})
        key = (person.c, person.u, person.link)
        if key in self.channel.notices:
            self.channel.notices[key] = self.api.clock()
        self.channel.revive()


@pytest.fixture
def world(db: Database, fake: WhatsAppFake) -> World:
    return World(db, fake)


def rows(reply: Any) -> list[str]:
    return list(reply[1])


def row_named(world: World, person: Person, title: str) -> str:
    """The id of the row titled ``title`` in the last list sent to ``person``."""
    bodies = [body for _, _, body in world.fake.requests if body["to"] == person.number.removeprefix("+")]
    listed = [r for s in bodies[-1]["interactive"]["action"]["sections"] for r in s["rows"]]
    return next(str(r["id"]) for r in listed if r["title"] == title)


def titles(world: World, person: Person) -> list[tuple[str, str]]:
    """The last list's rows as (title, description)."""
    bodies = [body for _, _, body in world.fake.requests if body["to"] == person.number.removeprefix("+")]
    listed = [r for s in bodies[-1]["interactive"]["action"]["sections"] for r in s["rows"]]
    return [(str(r["title"]), str(r.get("description", ""))) for r in listed]


def save_into(world: World, person: Person, folder_title: str) -> Any:
    """[Choose folder], the folder's row, [Save here]: the reply."""
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, folder_title))
    (save,) = [b for b in world.replies(person)[-1][1] if b.startswith("up:save:")]
    return world.tap(person, save)


# --- the menu ------------------------------------------------------------------------------------


def test_the_menu_has_ask_upload_and_get_and_more_and_help_are_typed(world: World) -> None:
    person = world.person()
    assert world.say(person, "Hi")[1] == ["ask.en", "upload.en", "get.en"]
    assert world.say(person, "More") == (
        say("more", "en"),
        ["menu.en", "menu.hi", "menu.mr", "help.en", "disconnect"],
    )
    assert world.say(person, "आणखी")[1] == ["menu.en", "menu.hi", "menu.mr", "help.mr", "disconnect"]
    assert world.say(person, "help") == (say("help", "en", APP_URL), ["another.en", "menu.en"])
    assert world.say(person, "मदद")[0] == say("help", "hi", APP_URL)
    assert [q for c, q in world.api.answers.asked if c == person.c] == []  # none of them is a question


def test_the_typing_indicator_shows_while_a_reply_is_made(world: World) -> None:
    person = world.person()
    hello = message(person.number, world.api.clock(), "Hi")
    world.send(person, hello)
    assert world.fake.typing == [hello["id"]]


class _Slow:
    """The chat, a little slow."""

    def __init__(self, chat: Chat, seconds: float) -> None:
        self.chat, self.seconds = chat, seconds

    def reply(self, linked: Linked, message: Waiting) -> Outgoing:
        time.sleep(self.seconds)
        return self.chat.reply(linked, message)

    def recheck(self, linked: Linked, reply: Outgoing) -> Outgoing:
        return self.chat.recheck(linked, reply)

    def notice(self, linked: Linked, message: Waiting) -> Outgoing | None:
        return self.chat.notice(linked, message)


def test_a_slow_reply_says_looking_first_and_a_quick_one_does_not(world: World) -> None:
    person = world.person()
    object.__setattr__(world.channel, "slow_seconds", 0.05)
    object.__setattr__(world.channel, "chat", _Slow(world.chat, 0.5))
    world.say(person, "नमस्कार")
    said = world.replies(person)
    assert said[-2] == say("looking", "mr") and said[-1][0] == say("menu", "mr", "Test")
    object.__setattr__(world.channel, "slow_seconds", 5.0)
    world.say(person, "Hi")
    lookings = [
        what for what in world.replies(person) if what in (say("looking", "mr"), say("looking", "en"))
    ]
    assert len(lookings) == 1


# --- one file into a folder ----------------------------------------------------------------------


def test_upload_asks_for_the_file_with_the_tip_the_first_time_only(world: World) -> None:
    person = world.person()
    assert world.tap(person, "upload.hi") == f"{say('send_now', 'hi')}\n{say('tip', 'hi')}"
    world.file(person, pdf("a"))
    assert world.tap(person, "upload.en") == say("send_now", "en")


def test_a_document_is_saved_in_the_folder_picked_through_the_browsers_upload(world: World) -> None:
    person = world.person()
    accounts = world.folder(person, "Accounts")
    bills = world.folder(person, "Bills 2026", accounts)
    world.folder(person, "March", bills)
    assert world.file(person, pdf("bill"), "Invoice_1123.pdf") == (
        'Save "Invoice_1123.pdf" to Kaagaz?',
        ["up:choose::en", "up:cancel::en"],  # no Let Kaagaz decide: auto-filing is off for them
    )
    world.tap(person, "up:choose::en")
    assert titles(world, person) == [("Accounts", "Accounts · 1 folder inside"), ("Type a folder name", "")]
    world.tap(person, row_named(world, person, "Accounts"))
    opened = [b for b in world.replies(person)[-1][1] if b.startswith("up:open:")]
    world.tap(person, opened[0])
    assert world.replies(person)[-1][0] == say("folders_in", "en", "Accounts")
    assert titles(world, person) == [
        ("Bills 2026", f"Accounts{PATH}Bills 2026 · 1 folder inside"),
        ("Back", ""),
        ("Type a folder name", ""),
    ]
    world.tap(person, row_named(world, person, "Bills 2026"))
    body, buttons = world.replies(person)[-1]
    assert body == f"Accounts{PATH}Bills 2026"
    assert [b.split(":")[1] for b in buttons] == ["save", "open", "back"]
    reply = world.tap(person, buttons[0])
    (saved,) = world.documents(person)
    link = f"{APP_URL}/documents/{saved['id']}"
    assert reply == (
        f'Saved "Invoice_1123.pdf" in Accounts{PATH}Bills 2026.\nOpen in Kaagaz: {link}',
        ["menu.en"],
    )
    assert (saved["name"], saved["folder"], saved["by"]) == ("Invoice_1123.pdf", bills, person.u)
    assert saved["batch"] is not None and world.api.queue.jobs[-1].document_id == saved["id"]  # the scan job
    assert world.api.storage.get(person.c, saved["id"]) == pdf("bill")
    (audited,) = [row for row in world.api.audit_rows(person.c) if row[0] == "document.upload"]
    assert audited[1:3] == ("document", saved["id"]) and audited[3]["via"] == "whatsapp"
    subject = _owner(
        world.db, "SELECT subject_user_id FROM audit_log WHERE target_id = :d", {"d": saved["id"]}
    )
    assert [str(row[0]) for row in subject] == [person.u]
    sent = [body["type"] for _, _, body in world.fake.requests]
    assert set(sent) <= {"text", "interactive"}  # the file itself never goes back on WhatsApp
    # The folder is a recent one now: first in the next list.
    world.file(person, pdf("next"), "next.pdf")
    world.tap(person, "up:choose::en")
    assert titles(world, person)[0] == ("Bills 2026", f"Recent · Accounts{PATH}Bills 2026 · 1 folder inside")


def test_a_whatsapp_upload_is_the_same_document_row_a_browser_upload_makes(world: World) -> None:
    person = world.person()
    folder = world.folder(person, "Bills")
    reply = person.signed.client.post(
        "/api/documents",
        files={"file": ("bill.pdf", pdf("browser"), "application/pdf")},
        data={"folder_id": folder, "batch_id": str(uuid.uuid4())},
        headers=CSRF,
    )
    assert reply.status_code == 201, reply.text
    world.file(person, pdf("whatsapp"), "bill.pdf")
    save_into(world, person, "Bills")
    columns = (
        "display_name, detected_type, folder_id, uploaded_by, read_requested_by, owner_id, version_no,"
        " family_id = id, status, autofile, held_for, department_id, private_folder_id, read_mode, read_model"
    )
    browser, whatsapp = _owner(
        world.db,
        f"SELECT {columns} FROM documents WHERE customer_id = :c ORDER BY created_at, id",  # noqa: S608
        {"c": person.c},
    )
    assert tuple(browser) == tuple(whatsapp)


# --- the list --------------------------------------------------------------------------------------


def test_the_list_pages_past_whatsapps_ten_rows(world: World) -> None:
    person = world.person()
    for n in range(12):
        world.folder(person, f"Folder {n:02d}")
    world.file(person, pdf("x"))
    world.tap(person, "up:choose::en")
    first = [title for title, _ in titles(world, person)]
    assert first == [f"Folder {n:02d}" for n in range(8)] + ["More folders", "Type a folder name"]
    world.tap(person, row_named(world, person, "More folders"))
    second = [title for title, _ in titles(world, person)]
    assert second == [f"Folder {n:02d}" for n in range(8, 12)] + ["Type a folder name"]
    assert len(first) <= 10


def test_a_recent_top_level_folder_is_listed_once(world: World) -> None:
    person = world.person()
    for n in range(10):
        world.folder(person, f"Folder {n:02d}")
    world.file(person, pdf("a"))
    save_into(world, person, "Folder 03")
    world.file(person, pdf("b"))
    world.tap(person, "up:choose::en")
    first = [title for title, _ in titles(world, person)]
    assert first[0] == "Folder 03" and first.count("Folder 03") == 1
    world.tap(person, row_named(world, person, "More folders"))
    shown = first + [title for title, _ in titles(world, person)]
    assert sorted(t for t in shown if t.startswith("Folder")) == [f"Folder {n:02d}" for n in range(10)]


def test_a_folder_name_typed_lists_those_named_so(world: World) -> None:
    person = world.person()
    world.folder(person, "Electricity bills")
    world.folder(person, "Phone bills")
    world.folder(person, "Salaries")
    world.file(person, pdf("x"))
    world.tap(person, "up:choose::en")
    assert world.tap(person, row_named(world, person, "Type a folder name")) == say("type_folder", "en")
    assert world.say(person, "BILLS")[0] == say("found_folders", "en")
    assert [t for t, _ in titles(world, person)] == ["Electricity bills", "Phone bills", "Type a folder name"]
    world.tap(person, row_named(world, person, "Type a folder name"))
    assert world.say(person, "receipts")[0] == say("no_folder_named", "en")
    assert [q for c, q in world.api.answers.asked if c == person.c] == []  # a folder's name, not a question


def test_back_goes_to_the_list_the_folder_was_picked_from(world: World) -> None:
    person = world.person()
    world.folder(person, "Alpha")
    world.folder(person, "Beta")
    world.file(person, pdf("x"))
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, "Beta"))
    body, buttons = world.replies(person)[-1]
    assert body == "Beta" and [b.split(":")[1] for b in buttons] == ["save", "back"]  # nothing inside
    world.tap(person, buttons[-1])
    assert [t for t, _ in titles(world, person)] == ["Alpha", "Beta", "Type a folder name"]


def test_let_kaagaz_decide_is_the_browsers_upload_with_no_folder(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_module.autofile, "applies", lambda conn, c, u: True)
    person = world.person()
    world.folder(person, "Bills")
    assert world.file(person, pdf("x"), "a.pdf")[1] == ["up:choose::en", "up:auto::en", "up:cancel::en"]
    world.tap(person, "up:choose::en")
    assert [t for t, _ in titles(world, person)][-1] == "Let Kaagaz decide"
    reply = world.tap(person, "up:auto::en")
    (saved,) = world.documents(person)
    assert saved["folder"] is None
    assert (
        reply[0]
        == f"{say('saved_unfiled', 'en', 'a.pdf')}\nOpen in Kaagaz: {APP_URL}/documents/{saved['id']}"
    )


# --- several files ---------------------------------------------------------------------------------


def test_files_sent_one_after_another_wait_and_are_answered_together(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    now = world.api.clock()
    first = file_message(person.number, now, world.fake.add_media(pdf("1")), filename="one.pdf")
    second = file_message(person.number, now, world.fake.add_media(pdf("2")), filename="two.pdf")
    assert post(world.client, delivery(first, second)).status_code == 200
    world.run()
    assert world.replies(person) == []  # more may come: nothing yet
    world.api.clock.advance(16)
    world.free(person)
    world.run()
    assert world.replies(person)[-1] == (say("save_many", "en", 2), ["up:choose::en", "up:cancel::en"])
    third = world.file(person, pdf("3"), "three.pdf")  # sent before the tap: it joins them
    assert third[0] == say("save_many", "en", 3)
    reply = save_into(world, person, "Bills")
    saved = world.documents(person)
    assert [d["name"] for d in saved] == ["one.pdf", "two.pdf", "three.pdf"]
    assert len({d["batch"] for d in saved}) == 1 and {d["folder"] for d in saved} == {saved[0]["folder"]}
    assert reply[0].startswith(say("saved_many", "en", 3, "Bills"))  # summed up, one line


def test_a_message_of_another_kind_ends_the_files_wait(world: World) -> None:
    person = world.person()
    now = world.api.clock()
    photo = file_message(person.number, now, world.fake.add_media(jpeg()), kind="image")
    world.send(person, photo, message(person.number, now + timedelta(seconds=1), "Hi"))
    said = world.replies(person)
    assert said[0][0] == say("save_one", "en", f"WhatsApp photo {_stamp(world, now)}") + "\n" + say(
        "tip", "en"
    )
    assert said[1][1] == ["ask.en", "upload.en", "get.en"]  # then the Hi, in order


def _stamp(world: World, moment: Any) -> str:
    from zoneinfo import ZoneInfo

    local = moment.astimezone(ZoneInfo(world.api.settings.timezone))
    return f"{local.day} {local.strftime('%b')} {local.year} {local.strftime('%H.%M')}"


def _photos(world: World, person: Person, count: int) -> list[bytes]:
    images = [jpeg(60 + n, 40) for n in range(count)]
    sent = world.api.clock() - timedelta(seconds=20)
    items = [
        file_message(person.number, sent + timedelta(seconds=n), world.fake.add_media(data), kind="image")
        for n, data in enumerate(images)
    ]
    assert world.send(person, *items) == (
        say("photos", "en", count) + "\n" + say("tip", "en"),
        ["up:one::en", "up:separate::en", "up:cancel::en"],
    )
    return images


def test_photos_as_one_document_are_one_pdf_in_the_order_sent(world: World) -> None:
    person = world.person()
    world.folder(person, "Scans")
    images = _photos(world, person, 3)
    world.tap(person, "up:one::en")
    world.tap(person, row_named(world, person, "Scans"))
    world.tap(person, world.replies(person)[-1][1][0])
    (saved,) = world.documents(person)
    assert saved["name"].endswith("(3 pages).pdf")
    data = world.api.storage.get(person.c, saved["id"])
    assert data.startswith(b"%PDF-") and [data.index(image) for image in images] == sorted(
        data.index(i) for i in images
    )
    document = pdfium.PdfDocument(data)
    try:
        assert len(document) == 3
    finally:
        document.close()
    notices = _owner(
        world.db,
        "SELECT count(*) FROM whatsapp_messages WHERE customer_id = :c AND document_id = :d",
        {"c": person.c, "d": saved["id"]},
    )
    assert notices == [(1,)]  # one notice for the one document


def test_photos_as_separate_files_are_one_batch(world: World) -> None:
    person = world.person()
    world.folder(person, "Scans")
    _photos(world, person, 2)
    world.tap(person, "up:separate::en")
    world.tap(person, row_named(world, person, "Scans"))
    world.tap(person, world.replies(person)[-1][1][0])
    saved = world.documents(person)
    assert len(saved) == 2 and saved[0]["batch"] == saved[1]["batch"]


def test_photos_joined_but_refused_by_the_upload_are_said_so(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = world.person()
    world.folder(person, "Scans")
    _photos(world, person, 2)
    world.tap(person, "up:one::en")
    world.tap(person, row_named(world, person, "Scans"))

    def full(*args: Any, **kwargs: Any) -> Any:
        raise ApiError(413, "storage_full", "full")

    monkeypatch.setattr(upload_module.documents, "store_upload", full)
    body, buttons = world.tap(person, world.replies(person)[-1][1][0])
    assert body.startswith('"WhatsApp photo ') and body.endswith(
        "(2 pages).pdf\" can't be saved: your organisation's Kaagaz storage is full. Ask your admin."
    )
    assert buttons == ["menu.en"] and world.documents(person) == []
    assert world.tap(person, "up:choose::en") == say("nothing_waiting", "en")  # answered: none wait


def test_photos_that_cant_be_joined_are_saved_one_by_one(world: World) -> None:
    person = world.person()
    world.folder(person, "Scans")
    sent = world.api.clock() - timedelta(seconds=20)
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, "PNG")
    items = [
        file_message(person.number, sent, world.fake.add_media(jpeg()), kind="image"),
        file_message(person.number, sent, world.fake.add_media(png.getvalue()), kind="image"),
    ]
    world.send(person, *items)
    world.tap(person, "up:one::en")
    world.tap(person, row_named(world, person, "Scans"))
    reply = world.tap(person, world.replies(person)[-1][1][0])
    assert reply[0].startswith(say("not_joined", "en")) and len(world.documents(person)) == 2


# --- refusals --------------------------------------------------------------------------------------


def test_cancel_saves_nothing(world: World) -> None:
    person = world.person()
    world.file(person, pdf("x"))
    assert world.tap(person, "up:cancel::en") == (say("cancelled", "en"), ["menu.en"])
    assert world.tap(person, "up:choose::en") == say("nothing_waiting", "en")
    assert world.documents(person) == [] and world.fake.fetched == []


def test_a_wait_past_its_15_minutes_saves_nothing(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    world.file(person, pdf("x"))
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, "Bills"))
    save = world.replies(person)[-1][1][0]
    _owner(
        world.db,
        "UPDATE whatsapp_pickers SET expires_at = now() - interval '1 second' WHERE user_id = :u",
        {"u": person.u},
    )
    assert world.tap(person, save) == say("nothing_waiting", "en")
    assert world.documents(person) == [] and world.fake.fetched == []


def test_uploads_switched_off_are_said_plainly_at_every_step(world: World) -> None:
    person = world.person(uploads=False)
    assert world.tap(person, "upload.en") == say("uploads_off", "en")
    assert world.file(person, pdf("x")) == say("uploads_off", "en")
    on = "UPDATE whatsapp_org_settings SET uploads = :on WHERE customer_id = :c"
    _owner(world.db, on, {"on": True, "c": person.c})
    world.folder(person, "Bills")
    world.file(person, pdf("y"))
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, "Bills"))
    save = world.replies(person)[-1][1][0]
    _owner(world.db, on, {"on": False, "c": person.c})  # switched off before the tap
    assert world.tap(person, save) == say("uploads_off", "en")
    assert world.documents(person) == [] and world.fake.fetched == []


def test_files_past_the_hourly_limit_are_refused(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_module, "MAX_FILES_AN_HOUR", 1)
    person = world.person()
    world.file(person, pdf("1"))
    world.tap(person, "up:cancel::en")  # nothing waits: only the hour's count stands in the way
    assert world.file(person, pdf("2")) == say("too_many_files", "en")


def test_a_video_is_not_saved(world: World) -> None:
    person = world.person()
    assert world.file(person, b"\x00\x00\x00\x18ftypmp42", None, kind="video") == say("no_video", "en")


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (b"MZ\x90\x00" + b"\x00" * 300, "wrong_type"),  # a program
        (b"", "empty_file"),
        (
            b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024),
            "too_large",
        ),  # over the photo limit, by its own check
    ],
)
def test_a_file_the_upload_refuses_is_said_so_and_nothing_is_kept(
    world: World, data: bytes, said: str
) -> None:
    person = world.person()
    world.folder(person, "Bills")
    world.file(person, data, "f.bin")
    reply = save_into(world, person, "Bills")
    assert reply == (say(said, "en", "f.bin"), ["menu.en"])
    assert world.documents(person) == []


def test_a_file_too_large_by_metas_size_is_never_fetched(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    media = world.fake.add_media(pdf("big"))
    world.fake.sizes[media] = (
        10**12
    )  # past every type's limit (the largest, as the browser's resumable start)
    sent = world.api.clock() - timedelta(seconds=20)
    world.send(person, file_message(person.number, sent, media, filename="big.pdf"))
    assert save_into(world, person, "Bills")[0] == say("too_large", "en", "big.pdf")
    assert world.fake.fetched == [] and world.documents(person) == []


def test_a_file_meta_no_longer_has_is_asked_for_again(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    world.file(person, pdf("x"), "old.pdf")
    world.fake.gone.update(world.fake.media)
    assert save_into(world, person, "Bills")[0] == say("media_gone", "en", "old.pdf")


def test_names_are_masked_in_the_reply(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    world.file(person, pdf("x"), "Account 123456789012.pdf")
    reply = save_into(world, person, "Bills")
    assert "123456789012" not in reply[0] and "••••9012" in reply[0]
    assert world.documents(person)[0]["name"] == "Account 123456789012.pdf"  # kept as it is


# --- duplicates, as the browser answers them -----------------------------------------------------------


def _browser_copy(world: World, person: Person, data: bytes, name: str) -> str:
    reply = person.signed.client.post(
        "/api/documents", files={"file": (name, data, "application/pdf")}, headers=CSRF
    )
    assert reply.status_code == 201, reply.text
    return str(reply.json()["document"]["id"])


def test_a_copy_the_person_sees_is_named_with_its_link(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    first = _browser_copy(world, person, pdf("same"), "first.pdf")
    world.file(person, pdf("same"), "again.pdf")
    reply = save_into(world, person, "Bills")
    assert reply[0] == f"{say('already', 'en', 'first.pdf')}\nOpen in Kaagaz: {APP_URL}/documents/{first}"
    assert len(world.documents(person)) == 1  # nothing new stored, nothing audited
    assert [row for row in world.api.audit_rows(person.c) if row[3].get("via") == "whatsapp"] == []


def test_a_copy_in_trash_is_named_and_a_locked_copy_never_is(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    trashed = _browser_copy(world, person, pdf("trash"), "trashed.pdf")
    locked = _browser_copy(world, person, pdf("locked"), "secret.pdf")
    _owner(
        world.db,
        "UPDATE documents SET deleted_at = now(), deleted_by = uploaded_by WHERE id = :d",
        {"d": trashed},
    )
    _owner(
        world.db,
        "UPDATE documents SET locked_at = now(), locked_by = uploaded_by WHERE id = :d",
        {"d": locked},
    )
    _owner(  # they may open it (in the browser), so it is theirs to see: still never named on WhatsApp
        world.db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": person.c, "d": locked, "u": person.u},
    )
    world.file(person, pdf("trash"), "a.pdf")
    assert save_into(world, person, "Bills")[0] == say("already_trash", "en", "trashed.pdf")
    world.file(person, pdf("locked"), "b.pdf")
    reply = save_into(world, person, "Bills")
    assert reply[0] == say("locked_copy", "en") and "secret" not in str(reply) and locked not in str(reply)
    # Locked and in Trash: still the locked words, never its name (locked is asked first).
    both = _browser_copy(world, person, pdf("locked trash"), "hush.pdf")
    _owner(
        world.db,
        "UPDATE documents SET locked_at = now(), locked_by = uploaded_by, deleted_at = now(),"
        " deleted_by = uploaded_by WHERE id = :d",
        {"d": both},
    )
    _owner(
        world.db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": person.c, "d": both, "u": person.u},
    )
    world.file(person, pdf("locked trash"), "c.pdf")
    reply = save_into(world, person, "Bills")
    assert reply[0] == say("locked_copy", "en") and "hush" not in str(reply) and both not in str(reply)


# --- notices -----------------------------------------------------------------------------------------


def _saved(world: World, person: Person) -> str:
    world.folder(person, "Bills")
    world.file(person, pdf(uuid.uuid4().hex), "bill.pdf")
    save_into(world, person, "Bills")
    (saved,) = world.documents(person)
    return saved["id"]


def test_the_ready_notice_goes_once_kaagaz_has_read_the_file(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    sent = len(world.replies(person))
    world.free(person)
    world.run()
    assert len(world.replies(person)) == sent  # still being read: nothing
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": document})
    world.free(person)
    world.run()
    link = f"{APP_URL}/documents/{document}"
    assert world.replies(person)[-1] == f"{say('ready', 'en', 'bill.pdf', 'Bills')}\nOpen in Kaagaz: {link}"
    world.free(person)
    world.run()
    assert len(world.replies(person)) == sent + 1  # once


def test_a_file_that_fails_the_virus_check_is_said_so(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    documents = PgDocuments(world.db)
    gate = ScanGate(world.api.storage, FakeScanner(Verdict.INFECTED), world.api.storage, documents)
    with pytest.raises(JobFailed):
        gate.check(Job(customer_id=person.c, document_id=document))
    world.free(person)
    world.run()
    assert world.replies(person)[-1] == say("rejected", "en", "bill.pdf")


def test_a_file_that_could_not_be_read_is_said_so_with_its_link(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    _owner(
        world.db, "UPDATE documents SET status = 'failed', failure_code = 'x' WHERE id = :d", {"d": document}
    )
    world.free(person)
    world.run()
    link = f"{APP_URL}/documents/{document}"
    assert world.replies(person)[-1] == f"{say('not_read', 'en', 'bill.pdf')}\nOpen in Kaagaz: {link}"


@pytest.mark.parametrize(
    "change", ["deleted_at = now(), deleted_by = uploaded_by", "locked_at = now(), locked_by = uploaded_by"]
)
def test_no_notice_goes_for_a_file_deleted_or_locked_since(world: World, change: str) -> None:
    person = world.person()
    document = _saved(world, person)
    sent = len(world.replies(person))
    _owner(world.db, f"UPDATE documents SET status = 'ready', {change} WHERE id = :d", {"d": document})  # noqa: S608
    world.free(person)
    world.run()
    assert len(world.replies(person)) == sent
    left = _owner(world.db, "SELECT state FROM whatsapp_messages WHERE document_id = :d", {"d": document})
    assert left == [("done",)]


def test_a_notice_not_sent_at_first_never_goes_once_its_file_is_hidden(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    sent = len(world.replies(person))
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": document})
    world.fake.failures = [500]  # Meta down: the notice is made, not sent, kept to try again
    world.free(person)
    world.run()
    _owner(
        world.db,
        "UPDATE documents SET deleted_at = now(), deleted_by = uploaded_by WHERE id = :d",
        {"d": document},
    )
    world.free(person)
    world.run()  # checked again just before the try: nothing goes, not even "couldn't find"
    assert len(world.replies(person)) == sent and world.fake.failures == []


def test_a_notice_past_the_24_hours_waits_for_the_persons_next_message(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    sent = len(world.replies(person))
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": document})
    world.api.clock.advance(25 * 3600)  # WhatsApp's window shut: nothing may go
    world.free(person)
    world.run()
    assert len(world.replies(person)) == sent
    kept = _owner(world.db, "SELECT state FROM whatsapp_messages WHERE document_id = :d", {"d": document})
    assert kept == [("expired",)]
    world.say(person, "Hi")  # the window open again: the notice goes after the reply
    said = world.replies(person)
    assert said[-2][1] == ["ask.en", "upload.en", "get.en"] and said[-1].startswith("Ready.")


def test_a_notice_waits_behind_the_persons_own_messages(world: World) -> None:
    person = world.person()
    document = _saved(world, person)
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": document})
    world.free(person)
    world.send(person, message(person.number, world.api.clock(), "Hi"))
    said = world.replies(person)
    assert said[-2][1] == ["ask.en", "upload.en", "get.en"] and said[-1].startswith("Ready.")


def test_a_ready_notice_is_said_in_the_chats_language(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    world.tap(person, "upload.mr")
    media = world.fake.add_media(pdf("mr"))
    world.send(
        person,
        file_message(person.number, world.api.clock() - timedelta(seconds=20), media, filename="b.pdf"),
    )
    world.tap(person, "up:choose::mr")
    world.tap(person, row_named(world, person, "Bills"))
    world.tap(person, world.replies(person)[-1][1][0])
    (saved,) = world.documents(person)
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": saved["id"]})
    world.free(person)
    world.run()
    assert world.replies(person)[-1].startswith(say("ready", "mr", "b.pdf", "Bills"))


def test_the_same_bytes_from_two_organisations_are_two_files(db: Database, fake: WhatsAppFake) -> None:
    world = World(db, fake)
    one, two = world.person(), world.person()
    shared = pdf(uuid.uuid4().hex)  # this test's own bytes: the database keeps other runs' rows
    _browser_copy(world, one, shared, "theirs.pdf")
    world.folder(two, "Bills")
    world.file(two, shared, "mine.pdf")
    reply = save_into(world, two, "Bills")
    assert reply[0].startswith(say("saved", "en", "mine.pdf", "Bills"))  # never "already in Kaagaz"
    digest = hashlib.sha256(shared).hexdigest()
    assert _owner(db, "SELECT count(*) FROM documents WHERE sha256 = :h", {"h": digest}) == [(2,)]


def test_the_waiting_files_are_the_persons_own_row(world: World) -> None:
    person = world.person()
    world.file(person, pdf("x"), "x.pdf")
    with world.api.app_db.user(person.c, person.u) as conn:
        state = conn.execute(
            text("SELECT state FROM whatsapp_pickers WHERE user_id = :u"), {"u": person.u}
        ).scalar_one()
    assert state["step"] == "upload" and [f["name"] for f in state["files"]] == ["x.pdf"]
    other = world.person(person.c)
    with world.api.app_db.user(person.c, other.u) as conn:  # own_user_only: another person reads none of it
        assert conn.execute(text("SELECT count(*) FROM whatsapp_pickers")).scalar_one() == 0


# --- the reviews (principal and second) ----------------------------------------------------------


def _buttons(world: World, person: Person) -> list[str]:
    return list(world.replies(person)[-1][1])


def test_an_old_save_card_from_an_earlier_upload_saves_nothing(world: World) -> None:
    person = world.person()
    world.folder(person, "Old")
    world.folder(person, "New")
    world.file(person, pdf("a"), "a.pdf")
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, "Old"))
    old_save = _buttons(world, person)[0]
    world.tap(person, old_save)  # a.pdf saved in Old
    world.file(person, pdf("b"), "b.pdf")  # today's upload, its own list and buttons
    world.tap(person, "up:choose::en")
    world.tap(person, row_named(world, person, "New"))
    assert world.tap(person, old_save)[0].startswith(say("list_changed", "en"))
    assert [d["name"] for d in world.documents(person)] == ["a.pdf"]


def test_the_waiting_list_is_capped(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_module, "MAX_FILES_AN_HOUR", 2)
    person = world.person()
    world.file(person, pdf("1"))
    world.file(person, pdf("2"))
    # Out of the hour's count, still waiting: the list itself is the limit too.
    _owner(
        world.db,
        "UPDATE whatsapp_messages SET received_at = now() - interval '2 hours' WHERE user_id = :u",
        {"u": person.u},
    )
    assert world.file(person, pdf("3")) == say("too_many_files", "en")


def test_a_long_save_renews_the_lease_between_files(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    person = world.person()
    world.folder(person, "Bills")
    leases: list[Any] = []
    real = upload_module.documents.store_upload

    def watched(*args: Any, **kwargs: Any) -> Any:
        found = _owner(world.db, "SELECT busy_until FROM whatsapp_links WHERE id = :l", {"l": person.link})
        leases.append(found[0][0])
        time.sleep(0.05)
        return real(*args, **kwargs)

    sent = world.api.clock() - timedelta(seconds=20)
    items = [
        file_message(person.number, sent, world.fake.add_media(pdf(f"lease {n}")), filename=f"{n}.pdf")
        for n in range(3)
    ]
    world.send(person, *items)
    monkeypatch.setattr(upload_module.documents, "store_upload", watched)
    save_into(world, person, "Bills")
    assert len(leases) == 3 and leases[0] < leases[1] < leases[2]


@pytest.mark.parametrize("down", ["meta", "storage"])
def test_a_batch_that_breaks_partway_keeps_what_was_saved_and_the_rest_waits(
    world: World, monkeypatch: pytest.MonkeyPatch, down: str
) -> None:
    person = world.person()
    world.folder(person, "Bills")
    sent = world.api.clock() - timedelta(seconds=20)
    second = world.fake.add_media(pdf(f"second {down}"))
    items = [
        file_message(person.number, sent, world.fake.add_media(pdf(f"first {down}")), filename="one.pdf"),
        file_message(person.number, sent, second, filename="two.pdf"),
    ]
    world.send(person, *items)
    real = upload_module.documents.store_upload
    if down == "meta":
        world.fake.broken.add(second)
    else:
        calls: list[int] = []

        def full(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 2:
                raise BackendUnavailable("storage")
            return real(*args, **kwargs)

        monkeypatch.setattr(upload_module.documents, "store_upload", full)
    body, buttons = save_into(world, person, "Bills")
    (saved,) = world.documents(person)
    assert body.startswith(say("fetch_failed", "en")) and say("saved", "en", "one.pdf", "Bills") in body
    assert buttons[0].startswith("up:save:") and buttons[1] == "menu.en"
    noticed = _owner(
        world.db, "SELECT count(*) FROM whatsapp_messages WHERE document_id = :d", {"d": saved["id"]}
    )
    recent = _owner(
        world.db, "SELECT recent_folder_ids FROM whatsapp_links WHERE id = :l", {"l": person.link}
    )
    assert noticed == [(1,)] and [str(f) for f in recent[0][0]] == [saved["folder"]]
    world.fake.broken.clear()
    monkeypatch.setattr(upload_module.documents, "store_upload", real)
    world.tap(person, buttons[0])  # Save the rest
    assert [d["name"] for d in world.documents(person)] == ["one.pdf", "two.pdf"]


def test_a_long_batch_names_every_refusal_first_and_sums_up_the_saved(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    sent = world.api.clock() - timedelta(seconds=20)
    items = [
        file_message(
            person.number, sent, world.fake.add_media(b"MZ" + bytes(300)), filename=f"{'x' * 60} {n}.exe"
        )
        for n in range(12)
    ]
    items += [
        file_message(person.number, sent, world.fake.add_media(pdf(f"ok {n}")), filename=f"ok{n}.pdf")
        for n in range(3)
    ]
    world.send(person, *items)
    body, _ = save_into(world, person, "Bills")
    assert len(body) <= 1024 and "…" in body
    assert body.startswith(say("wrong_type", "en", f"{'x' * 60} 0.exe"))
    assert body.endswith(say("saved_many", "en", 3, "Bills"))  # never cut off


def test_a_file_handled_again_is_not_added_twice(world: World) -> None:
    person = world.person()
    world.file(person, pdf("once"), "once.pdf")
    # A restart before its reply was done: the message waits again and is handled again.
    _owner(
        world.db,
        "UPDATE whatsapp_messages SET state = 'waiting', body = 'once.pdf'"
        " WHERE user_id = :u AND kind = 'document'",
        {"u": person.u},
    )
    world.channel.made.clear()
    world.free(person)
    world.run()
    with world.api.app_db.user(person.c, person.u) as conn:
        state = conn.execute(
            text("SELECT state FROM whatsapp_pickers WHERE user_id = :u"), {"u": person.u}
        ).scalar_one()
    assert [f["name"] for f in state["files"]] == ["once.pdf"]


def test_a_document_named_like_a_command_is_only_a_file(world: World) -> None:
    person = world.person()
    for name in ("STOP", "disconnect", "keep", "disconnect_yes"):
        assert world.file(person, pdf(name), name)[0] == say("save_one", "en", name)
        world.tap(person, "up:cancel::en")
    live = _owner(world.db, "SELECT ended_at FROM whatsapp_links WHERE id = :l", {"l": person.link})
    assert live == [(None,)]


class _Flaky:
    """The chat, slow; its first reply breaks (after LOOKING went)."""

    def __init__(self, chat: Chat) -> None:
        self.chat, self.calls = chat, 0

    def reply(self, linked: Linked, message: Waiting) -> Outgoing:
        self.calls += 1
        time.sleep(0.3)
        if self.calls == 1:
            raise RuntimeError("a bug while answering")
        return self.chat.reply(linked, message)

    def recheck(self, linked: Linked, reply: Outgoing) -> Outgoing:
        return self.chat.recheck(linked, reply)

    def notice(self, linked: Linked, message: Waiting) -> Outgoing | None:
        return self.chat.notice(linked, message)


def test_looking_goes_once_a_message_and_never_for_an_uploads_tap(world: World) -> None:
    person = world.person()
    object.__setattr__(world.channel, "slow_seconds", 0.05)
    object.__setattr__(world.channel, "chat", _Flaky(world.chat))
    assert post(world.client, delivery(message(person.number, world.api.clock(), "Hi"))).status_code == 200
    with pytest.raises(RuntimeError):
        world.run()
    world.free(person)
    world.run()  # tried again: made this time, and slow again
    lookings = [what for what in world.replies(person) if what == say("looking", "en")]
    assert len(lookings) == 1 and world.replies(person)[-1][1] == ["ask.en", "upload.en", "get.en"]
    object.__setattr__(world.channel, "chat", _Slow(world.chat, 0.3))
    world.file(person, pdf("x"))
    before = len(world.replies(person))
    world.tap(person, "up:cancel::en")
    assert world.replies(person)[before:] == [(say("cancelled", "en"), ["menu.en"])]


def test_a_notice_is_looked_at_again_later_as_it_ages(world: World) -> None:
    person = world.person()
    _saved(world, person)  # its file sent 20 seconds ago, still being read: put aside
    key = (person.c, person.u, person.link)
    assert world.channel.notices[key] - world.api.clock() == timedelta(seconds=5)  # 3 + 20 // 10
    world.api.clock.advance(3600)
    world.free(person)  # its time came: back in line, still being read, put aside again
    world.run()
    assert world.channel.notices[key] - world.api.clock() == timedelta(seconds=60)
    world.api.clock.advance(30)
    assert world.channel.revive() == 0  # not yet


def test_a_waiting_notice_never_delays_the_persons_own_messages(world: World) -> None:
    person = world.person()
    document = _saved(world, person)  # its notice waits: the file is read slowly
    busy = _owner(world.db, "SELECT busy_until FROM whatsapp_links WHERE id = :l", {"l": person.link})
    assert busy == [(None,)]  # the lease freed, not held while the file is read
    assert post(world.client, delivery(message(person.number, world.api.clock(), "Hi"))).status_code == 200
    world.run()  # one turn, nothing freed by hand
    assert world.replies(person)[-1][1] == ["ask.en", "upload.en", "get.en"]
    _owner(world.db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": document})
    world.api.clock.advance(5)
    assert world.channel.revive() == 1  # its time came: back in line
    world.run()
    assert world.replies(person)[-1].startswith("Ready.")


def test_one_ready_notice_for_a_batch_once_all_are_read(world: World) -> None:
    person = world.person()
    world.folder(person, "Bills")
    sent = world.api.clock() - timedelta(seconds=20)
    items = [
        file_message(person.number, sent, world.fake.add_media(pdf(f"batch {n}")), filename=f"{n}.pdf")
        for n in range(3)
    ]
    world.send(person, *items)
    save_into(world, person, "Bills")
    ids = [d["id"] for d in world.documents(person)]
    before = len(world.replies(person))
    _owner(
        world.db, "UPDATE documents SET status = 'ready' WHERE id = ANY(CAST(:d AS uuid[]))", {"d": ids[:2]}
    )
    world.free(person)
    world.run()
    assert len(world.replies(person)) == before  # one still being read: nothing yet
    _owner(world.db, "UPDATE documents SET status = 'rejected' WHERE id = :d", {"d": ids[2]})
    world.free(person)
    world.run()
    said = world.replies(person)[before:]
    assert said == [
        f"{say('rejected', 'en', '2.pdf')}\n\n{say('ready_many', 'en', 2, 'Bills')}"
        f"\nOpen in Kaagaz: {APP_URL}/documents"
    ]
    left = _owner(
        world.db,
        "SELECT DISTINCT state FROM whatsapp_messages WHERE document_id = ANY(CAST(:d AS uuid[]))",
        {"d": ids},
    )
    assert left == [("done",)]


def test_an_upload_tap_with_nothing_waiting_answers_in_its_language(world: World) -> None:
    person = world.person()
    assert world.tap(person, "up:choose::hi") == say("nothing_waiting", "hi")


def test_photos_past_the_pdf_limit_are_saved_one_by_one(db: Database, fake: WhatsAppFake) -> None:
    world = World(db, fake, max_upload_bytes=1200)
    person = world.person()
    world.folder(person, "Scans")
    _photos(world, person, 2)
    world.tap(person, "up:one::en")
    world.tap(person, row_named(world, person, "Scans"))
    reply = world.tap(person, _buttons(world, person)[0])
    assert reply[0].startswith(say("not_joined", "en")) and len(world.documents(person)) == 2
