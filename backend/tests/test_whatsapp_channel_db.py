"""The WhatsApp channel (#603, PRD 7 section 15 W2), end to end on the fake WhatsApp: a signed
message through the webhook, claimed, answered only to the connected number and marked done; one
person's messages one at a time and in WhatsApp's order, different people in one claim; every
message checks the connection again, and a refused one gets "not connected" once and ends; a
message past WhatsApp's 24 hours expires unanswered; a reply that can't be sent waits, keeps the
order, and fails after five tries. And the process loop (``channel_main.run``)."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any, cast

import pytest
from sqlalchemy import text

from kaagaz import channel_main
from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.channel.loop import MAX_TRIES, NOT_CONNECTED, PLACEHOLDER, Channel
from kaagaz.db.engine import Database
from kaagaz.db.whatsapp import Linked
from kaagaz.ingestion.errors import BackendUnavailable
from tests.test_api_support import Api
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


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def _owner(db: Database, statement: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return [tuple(row) for row in result] if result.returns_rows else []


class _World:
    """An API on the fake WhatsApp, its channel in the API's role, and connected people."""

    def __init__(self, db: Database, fake: WhatsAppFake) -> None:
        self.db, self.fake = db, fake
        self.api = Api(db, settings=settings_for(fake))
        self.client = self.api.client()
        self.channel = Channel(
            db=self.api.app_db,
            sender=MetaWhatsApp.from_settings(self.api.settings),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
        )

    def person(self, customer: str | None = None, role: str = "member") -> tuple[str, str, str, str]:
        """(customer, person, number, link) of a newly connected person."""
        if customer is None:
            customer, _ = self.api.customer()
        person, _ = self.api.add_user(customer, role)
        number = new_number()
        return customer, person, number, connect(self.db, customer, person, number)

    def send(self, number: str, *messages: dict[str, Any]) -> None:
        assert post(self.client, delivery(*messages)).status_code == 200

    def run(self) -> list[Linked]:
        """Claim and handle until nothing is waiting (other tests' strays included)."""
        handled: list[Linked] = []
        while claimed := self.channel.claim(100):
            for linked in claimed:
                self.channel.handle(linked)
            handled += claimed
        return handled

    def texts_to(self, number: str) -> list[str]:
        return [body for to, body in self.fake.texts() if to == number.removeprefix("+")]

    def messages(self, link: str) -> list[tuple[Any, ...]]:
        return _owner(
            self.db,
            "SELECT wamid, state, body IS NULL, reply_sent_at IS NOT NULL, tries FROM whatsapp_messages"
            " WHERE link_id = :l ORDER BY reply_sent_at NULLS LAST, wamid",
            {"l": link},
        )

    def link(self, link: str) -> tuple[Any, ...]:
        return _owner(
            self.db,
            "SELECT end_reason, phone_sealed IS NULL, busy_until IS NULL, last_used_at IS NOT NULL"
            " FROM whatsapp_links WHERE id = :l",
            {"l": link},
        )[0]


def test_a_message_goes_from_the_webhook_to_a_reply_to_the_connected_number(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), "Hi", wamid="wamid.hi"))
    assert Linked(customer, person, link) in world.run()
    assert world.texts_to(number) == [PLACEHOLDER]
    assert world.messages(link) == [("wamid.hi", "done", True, True, 0)]
    assert world.link(link) == (None, False, True, True)  # live, lease free, last used now
    world.run()
    assert world.texts_to(number) == [PLACEHOLDER]  # answered once


def test_one_persons_messages_are_answered_in_whatsapps_order(db: Database, fake: WhatsAppFake) -> None:
    """Meta delivers in any order: the second message arrives first, and is answered second."""
    world = _World(db, fake)
    _, _, number, link = world.person()
    now = world.api.clock()
    world.send(number, message(number, now, "second", wamid="wamid.2"))
    world.send(number, message(number, now - timedelta(seconds=5), "first", wamid="wamid.1"))
    world.run()
    assert [row[0] for row in world.messages(link)] == ["wamid.1", "wamid.2"]  # by reply_sent_at
    assert world.texts_to(number) == [PLACEHOLDER, PLACEHOLDER]


def test_a_person_is_handled_by_one_process_at_a_time_and_different_people_together(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    a = world.person()
    b = world.person()
    for _, _, number, _ in (a, b):
        world.send(number, message(number, world.api.clock()))
    first = world.channel.claim(100)
    assert {Linked(*a[:2], a[3]), Linked(*b[:2], b[3])} <= set(first)  # side by side, in one claim
    assert Linked(*a[:2], a[3]) not in world.channel.claim(100)  # leased: no second process
    _owner(
        db, "UPDATE whatsapp_links SET busy_until = now() - interval '1 second' WHERE id = :l", {"l": a[3]}
    )
    assert Linked(*a[:2], a[3]) in world.channel.claim(100)  # a crashed process's lease ran out
    # Nobody handled these claims: free both leases, as running out would, and answer.
    _owner(
        db,
        "UPDATE whatsapp_links SET busy_until = NULL WHERE id = ANY(CAST(:l AS uuid[]))",
        {"l": [a[3], b[3]]},
    )
    world.run()
    assert world.texts_to(a[2]) == world.texts_to(b[2]) == [PLACEHOLDER]


def _refuse(world: _World, how: str, customer: str, person: str) -> None:
    settings = "UPDATE whatsapp_org_settings SET {} WHERE customer_id = :c"
    owner = {
        "disabled": "UPDATE users SET disabled = true WHERE id = :u",
        "signed_out_everywhere": "UPDATE users SET session_version = session_version + 1 WHERE id = :u",
        "whatsapp_off": settings.format("enabled = false"),
        "role_not_ticked": settings.format("roles = ARRAY['reviewer']"),
        "organisation_disabled": "UPDATE customers SET status = 'disabled', disabled_at = now()"
        " WHERE id = :c",
    }[how]
    _owner(world.db, owner, {"u": person, "c": customer})


@pytest.mark.parametrize(
    ("how", "reason"),
    [
        ("disabled", "account"),
        ("organisation_disabled", "account"),
        ("signed_out_everywhere", "session"),
        ("whatsapp_off", "not_allowed"),
        ("role_not_ticked", "not_allowed"),
    ],
)
def test_every_message_checks_again_and_a_refused_connection_is_told_once_and_ends(
    db: Database, fake: WhatsAppFake, how: str, reason: str
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), "one", wamid=f"wamid.{how}.1"))
    world.send(number, message(number, world.api.clock(), "two", wamid=f"wamid.{how}.2"))
    _refuse(world, how, customer, person)
    world.run()
    assert world.texts_to(number) == [NOT_CONNECTED]  # once, then nothing to a number gone
    assert world.link(link) == (reason, True, True, True)  # ended, its number gone, lease free
    assert [row[:4] for row in world.messages(link)] == [
        (f"wamid.{how}.1", "done", True, True),
        (f"wamid.{how}.2", "done", True, False),
    ]
    audit = _owner(
        db,
        "SELECT subject_user_id::text, details->>'reason' FROM audit_log"
        " WHERE customer_id = :c AND action = 'whatsapp.disconnected'",
        {"c": customer},
    )
    assert audit == [(person, reason)]
    world.send(number, message(number, world.api.clock(), "three", wamid=f"wamid.{how}.3"))
    assert len(world.messages(link)) == 2  # no longer connected: the webhook stores nothing


def test_a_message_past_whatsapps_24_hours_expires_unanswered(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    _, _, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.late"))
    world.api.clock.advance(24 * 3600 + 1)
    world.run()
    assert world.texts_to(number) == []
    assert world.messages(link) == [("wamid.late", "expired", True, False, 0)]


def test_a_reply_not_sent_waits_keeps_the_order_and_fails_after_five_tries(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    now = world.api.clock()
    world.send(
        number,
        message(number, now - timedelta(seconds=1), wamid="wamid.a"),
        message(number, now, wamid="wamid.b"),
    )
    linked = Linked(customer, person, link)
    fake.failures = [500]
    world.channel.handle(linked)
    assert world.texts_to(number) == []
    assert world.messages(link) == [
        ("wamid.a", "waiting", False, False, 1),
        ("wamid.b", "waiting", False, False, 0),
    ]
    assert world.link(link)[2] is False  # resting: the lease is kept a while
    assert linked not in world.channel.claim(100)
    fake.failures = [503] * (MAX_TRIES - 1) + [400]
    for _ in range(MAX_TRIES - 1):
        world.channel.handle(linked)
    assert world.messages(link)[0] == ("wamid.a", "failed", True, False, MAX_TRIES)
    assert world.texts_to(number) == []  # b's first try failed (the 400) after a's last
    world.channel.handle(linked)
    assert world.messages(link) == [
        ("wamid.b", "done", True, True, 1),
        ("wamid.a", "failed", True, False, MAX_TRIES),
    ]
    assert world.texts_to(number) == [PLACEHOLDER]


class _Loop:
    """A channel for ``channel_main.run``: the claims it is given, then nothing; records the rest."""

    def __init__(self, claims: list[list[Linked] | Exception]) -> None:
        self.claims = claims
        self.handled: list[Linked] = []
        self.sweeps = 0
        self.done = threading.Event()

    def claim(self, limit: int) -> list[Linked]:
        if not self.claims:
            self.done.set()
            return []
        claim = self.claims.pop(0)
        if isinstance(claim, Exception):
            raise claim
        return claim

    def handle(self, linked: Linked) -> None:
        self.handled.append(linked)
        if linked.link_id == "boom":
            raise RuntimeError("one person's failure")

    def sweep(self) -> int:
        self.sweeps += 1
        return 0


def test_the_loop_sweeps_claims_and_handles_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_main, "PAUSE_SECONDS", 0.01)
    monkeypatch.setattr(channel_main, "POLL_SECONDS", 0.01)
    people = [Linked("c", "u", "one"), Linked("c", "u", "boom"), Linked("c", "u", "two")]
    loop = _Loop([people[:2], BackendUnavailable("database"), people[2:]])
    stop = threading.Event()
    running = threading.Thread(target=channel_main.run, args=(cast(Channel, loop), stop, 2))
    running.start()
    assert loop.done.wait(10)
    stop.set()
    running.join(10)
    assert not running.is_alive()
    assert sorted(person.link_id for person in loop.handled) == [
        "boom",
        "one",
        "two",
    ]  # one failure stops nothing
    assert loop.sweeps == 1  # at start, then hourly
