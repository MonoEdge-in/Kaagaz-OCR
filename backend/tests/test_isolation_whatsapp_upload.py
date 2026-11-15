"""Kaagaz on WhatsApp's folder browser and uploads reach only what the person can see (#603 W5, PRD 7
section 15 "Tests and done when"), on PRD 7's walled organisation (``_walled``: Finance Department
only, Salaries private inside it, Sales and Loose open), through the fake WhatsApp, the webhook and
the channel in the API's role.

* The picker lists, counts and paths only folders the person sees: the plain reviewer never sees
  Finance or Salaries; Salaries' named person sees it at the top level with no path through Finance;
  Finance's member sees Finance with nothing inside, its head with Salaries inside. A typed name
  finds only what they see.
* A folder hidden between the list and the tap, or between its buttons and [Save here], a made-up
  row id and a row naming a hidden or another organisation's folder all give "isn't available any
  more"; an old list gives "That list has changed". Nothing is fetched or saved.
* A batch whose folder becomes hidden partway stops; the rest wait for a new choice.
* A copy of a file the person can't see gets HIDDEN_COPY, naming nothing.
* Another organisation's folders never appear.
"""

import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from PIL import Image

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.api.routers import documents as documents_router
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.channel.texts import say
from kaagaz.db.engine import Database
from tests.test_api_support import Api
from tests.test_isolation_access_views import Org, _walled
from tests.test_operator_auth_db import _owner
from tests.whatsapp_fake import (
    NUMBER_KEY,
    WhatsAppFake,
    connect,
    delivery,
    file_message,
    new_number,
    post,
    running_fake,
)
from tests.whatsapp_fake import whatsapp_settings as settings_for

pytestmark = pytest.mark.db

GONE = say("folder_gone", "en")
CHANGED = say("list_changed", "en")
_UNNAME = "DELETE FROM private_folder_members WHERE folder_id = :f AND user_id = :u"


class Walled:
    def __init__(self, db: Database, fake: WhatsAppFake) -> None:
        self.db, self.fake = db, fake
        self.api = Api(db, settings=settings_for(fake))
        self.org: Org = _walled(self.api)
        self.client = self.api.client()
        self.channel = Channel(
            db=self.api.app_db,
            sender=MetaWhatsApp.from_settings(self.api.settings),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
            chat=Chat(self.api.app.state.kaagaz),
        )
        self.numbers: dict[str, str] = {}
        for name in ("plain", "named", "member", "head"):
            self.numbers[name] = new_number()
            connect(db, self.org.c, self.org.people[name].user_id, self.numbers[name])
        _owner(
            db, "UPDATE whatsapp_org_settings SET uploads = true WHERE customer_id = :c", {"c": self.org.c}
        )

    def send(self, person: str, item: dict[str, Any]) -> Any:
        assert post(self.client, delivery(item)).status_code == 200
        _owner(
            self.db, "UPDATE whatsapp_links SET busy_until = NULL WHERE customer_id = :c", {"c": self.org.c}
        )
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)
        return self.replies(person)[-1]

    def file(self, person: str, data: bytes, name: str) -> Any:
        media = self.fake.add_media(data)
        sent = self.api.clock() - timedelta(seconds=20)
        return self.send(person, file_message(self.numbers[person], sent, media, filename=name))

    def tap(self, person: str, tapped: str) -> Any:
        return self.send(
            person,
            {
                "from": self.numbers[person].removeprefix("+"),
                "id": f"wamid.{uuid.uuid4().hex}",
                "timestamp": str(int(self.api.clock().timestamp())),
                "type": "interactive",
                "interactive": {"type": "list_reply", "list_reply": {"id": tapped, "title": "x"}},
            },
        )

    def say(self, person: str, words: str) -> Any:
        return self.send(
            person,
            {
                "from": self.numbers[person].removeprefix("+"),
                "id": f"wamid.{uuid.uuid4().hex}",
                "timestamp": str(int(self.api.clock().timestamp())),
                "type": "text",
                "text": {"body": words},
            },
        )

    def replies(self, person: str) -> list[Any]:
        to = self.numbers[person].removeprefix("+")
        return [what for number, what in self.fake.sent() if number == to]

    def rows(self, person: str) -> list[tuple[str, str, str]]:
        """The last list's rows sent to ``person``: (id, title, description)."""
        to = self.numbers[person].removeprefix("+")
        body = [b for _, _, b in self.fake.requests if b["to"] == to][-1]
        listed = [r for s in body["interactive"]["action"]["sections"] for r in s["rows"]]
        return [(str(r["id"]), str(r["title"]), str(r.get("description", ""))) for r in listed]

    def row(self, person: str, title: str) -> str:
        return next(row_id for row_id, shown, _ in self.rows(person) if shown == title)

    def saved(self, person: str) -> list[tuple[str, str | None]]:
        rows = _owner(
            self.db,
            "SELECT display_name, folder_id FROM documents WHERE uploaded_by = :u ORDER BY created_at, id",
            {"u": self.org.people[person].user_id},
        )
        return [(str(r[0]), None if r[1] is None else str(r[1])) for r in rows]

    def sent_to(self, person: str) -> str:
        to = self.numbers[person].removeprefix("+")
        return json.dumps(
            [b for _, _, b in self.fake.requests if b["to"] == to], ensure_ascii=False
        ).casefold()


@pytest.fixture
def walled(db: Database) -> Iterator[Walled]:
    with running_fake() as fake:
        yield Walled(db, fake)


def _choose(walled: Walled, person: str, name: str = "bill.pdf") -> None:
    walled.file(person, b"%PDF-1.7\n% " + uuid.uuid4().hex.encode(), name)
    walled.tap(person, "up:choose::en")


def test_the_picker_lists_counts_and_paths_only_folders_the_person_sees(walled: Walled) -> None:
    _choose(walled, "plain")
    assert [(t, d) for _, t, d in walled.rows("plain")] == [
        ("Loose", "Loose"),
        ("Sales", "Sales"),
        ("Type a folder name", ""),
    ]
    assert "finance" not in walled.sent_to("plain") and "salar" not in walled.sent_to("plain")
    _choose(walled, "named")  # Salaries' parent is hidden from them: it is at the top, with no path
    assert [(t, d) for _, t, d in walled.rows("named")][:3] == [
        ("Loose", "Loose"),
        ("Salaries", ""),
        ("Sales", "Sales"),
    ]
    assert "finance" not in walled.sent_to("named")
    walled.tap("named", walled.row("named", "Salaries"))
    assert walled.replies("named")[-1][0] == "Salaries"  # its buttons name it alone, never Finance's path
    _choose(walled, "member")  # Finance, with nothing inside they see
    assert ("Finance", "Finance") in [(t, d) for _, t, d in walled.rows("member")]
    walled.tap("member", walled.row("member", "Finance"))
    assert [b.split(":")[1] for b in walled.replies("member")[-1][1]] == ["save", "back"]
    _choose(walled, "head")  # the head sees Salaries inside Finance
    assert ("Finance", "Finance · 1 folder inside") in [(t, d) for _, t, d in walled.rows("head")]


def test_a_typed_name_finds_only_folders_the_person_sees(walled: Walled) -> None:
    _choose(walled, "plain")
    walled.tap("plain", walled.row("plain", "Type a folder name"))
    assert walled.say("plain", "Sal")[0] == say("found_folders", "en")
    assert [t for _, t, _ in walled.rows("plain")] == ["Sales", "Type a folder name"]
    walled.tap("plain", walled.row("plain", "Type a folder name"))
    assert walled.say("plain", "fin")[0] == say("no_folder_named", "en")
    assert "salaries" not in walled.sent_to("plain") and "finance" not in walled.sent_to("plain")


def test_a_folder_hidden_between_the_list_and_the_tap_gives_choose_again(walled: Walled) -> None:
    _choose(walled, "named")
    salaries = walled.row("named", "Salaries")
    _owner(walled.db, _UNNAME, {"f": walled.org.folders["salaries"], "u": walled.org.people["named"].user_id})
    body, _ = walled.tap("named", salaries)
    assert body.startswith(GONE)
    assert "Salaries" not in [t for _, t, _ in walled.rows("named")]  # the fresh list, without it
    assert walled.saved("named") == [] and walled.fake.fetched == []


def test_a_folder_hidden_between_its_buttons_and_save_saves_nothing(walled: Walled) -> None:
    _choose(walled, "named")
    walled.tap("named", walled.row("named", "Salaries"))
    save = walled.replies("named")[-1][1][0]
    _owner(walled.db, _UNNAME, {"f": walled.org.folders["salaries"], "u": walled.org.people["named"].user_id})
    assert walled.tap("named", save)[0].startswith(GONE)
    assert walled.saved("named") == [] and walled.fake.fetched == []
    walled.tap("named", walled.row("named", "Loose"))  # the file still waits for a new choice
    walled.tap("named", walled.replies("named")[-1][1][0])
    assert walled.saved("named") == [("bill.pdf", walled.org.folders["loose"])]


def test_a_made_up_row_a_hidden_folder_s_id_and_an_old_list_save_nothing(walled: Walled) -> None:
    _choose(walled, "plain")
    first = walled.rows("plain")[0][0]
    for forged in (
        "deadbeef",  # a row id that never was
        walled.org.folders["finance"],  # a hidden folder's own id
        str(uuid.uuid4()),  # any id at all, of this organisation or another
    ):
        gen = walled.rows("plain")[0][0].split(":")[2]  # the list just sent
        assert walled.tap("plain", f"up:f:{gen}:{forged}:en")[0].startswith(GONE), forged
    old = walled.tap("plain", first)  # a row of the first list, after newer ones were sent
    assert old[0].startswith(CHANGED)
    assert walled.saved("plain") == [] and walled.fake.fetched == []


def test_a_batch_whose_folder_is_hidden_partway_stops(
    walled: Walled, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = documents_router.store_upload
    calls: list[str] = []

    def hidden_after_the_first(*args: Any, **kwargs: Any) -> Any:
        if calls:
            _owner(
                walled.db,
                _UNNAME,
                {"f": walled.org.folders["salaries"], "u": walled.org.people["named"].user_id},
            )
        calls.append("x")
        return real(*args, **kwargs)

    sent = walled.api.clock() - timedelta(seconds=20)
    number = walled.numbers["named"]
    for name in ("one.pdf", "two.pdf"):
        media = walled.fake.add_media(b"%PDF-1.7\n% " + uuid.uuid4().hex.encode())
        assert (
            post(walled.client, delivery(file_message(number, sent, media, filename=name))).status_code == 200
        )
    walled.tap("named", "up:choose::en")  # handles the two files first, then the tap
    walled.tap("named", walled.row("named", "Salaries"))
    monkeypatch.setattr(documents_router, "store_upload", hidden_after_the_first)
    body, _ = walled.tap("named", walled.replies("named")[-1][1][0])
    assert body.startswith(say("saved", "en", "one.pdf", "Salaries")) and GONE in body
    assert walled.saved("named") == [("one.pdf", walled.org.folders["salaries"])]
    walled.tap("named", walled.row("named", "Loose"))
    walled.tap("named", walled.replies("named")[-1][1][0])
    assert walled.saved("named")[1:] == [("two.pdf", walled.org.folders["loose"])]  # the rest waited


def test_photos_as_one_document_into_a_folder_hidden_just_before_it_is_stored_all_wait(
    walled: Walled, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = documents_router.store_upload

    def hidden_first(*args: Any, **kwargs: Any) -> Any:  # after the photos are fetched and joined
        _owner(
            walled.db, _UNNAME, {"f": walled.org.folders["salaries"], "u": walled.org.people["named"].user_id}
        )
        return real(*args, **kwargs)

    out = io.BytesIO()
    Image.new("RGB", (30, 20)).save(out, "JPEG")
    sent = walled.api.clock() - timedelta(seconds=20)
    number = walled.numbers["named"]
    for _ in range(2):
        photo = file_message(number, sent, walled.fake.add_media(out.getvalue()), kind="image")
        assert post(walled.client, delivery(photo)).status_code == 200
    walled.tap("named", "up:one::en")
    walled.tap("named", walled.row("named", "Salaries"))
    monkeypatch.setattr(documents_router, "store_upload", hidden_first)
    assert walled.tap("named", walled.replies("named")[-1][1][0])[0].startswith(GONE)
    assert walled.saved("named") == []
    monkeypatch.setattr(documents_router, "store_upload", real)
    walled.tap("named", walled.row("named", "Loose"))
    walled.tap("named", walled.replies("named")[-1][1][0])
    (saved,) = walled.saved("named")
    assert saved[0].endswith("(2 pages).pdf") and saved[1] == walled.org.folders["loose"]


def test_a_copy_of_a_file_the_person_cant_see_names_nothing(walled: Walled) -> None:
    data = b"%PDF-1.7\n% finance's own " + uuid.uuid4().hex.encode()
    hidden = walled.org.files["fin"]
    _owner(
        walled.db,
        "UPDATE documents SET sha256 = :h WHERE id = :d",
        {"h": hashlib.sha256(data).hexdigest(), "d": hidden},
    )
    walled.file("plain", data, "copy.pdf")
    walled.tap("plain", "up:choose::en")
    walled.tap("plain", walled.row("plain", "Loose"))
    reply = walled.tap("plain", walled.replies("plain")[-1][1][0])
    assert reply == (say("hidden_copy", "en"), ["menu.en"])
    assert hidden not in walled.sent_to("plain") and "fin.pdf" not in walled.sent_to("plain")
    assert walled.saved("plain") == []


def test_another_organisations_folders_never_appear(walled: Walled) -> None:
    customer, _ = walled.api.customer()
    stranger = walled.api.sign_in(customer, "member")
    walled.numbers["stranger"] = new_number()
    connect(walled.db, customer, stranger.user_id, walled.numbers["stranger"])
    _owner(
        walled.db, "UPDATE whatsapp_org_settings SET uploads = true WHERE customer_id = :c", {"c": customer}
    )
    _choose(walled, "stranger")
    assert [t for _, t, _ in walled.rows("stranger")] == ["Type a folder name"]
    for name in ("loose", "sales", "finance", "salar"):
        assert name not in walled.sent_to("stranger")
