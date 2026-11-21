"""The WhatsApp channel (#603, PRD 7 section 15 W2), end to end on the fake WhatsApp: a signed
message through the webhook, claimed, answered only to the connected number and marked done; one
person's messages one at a time and in WhatsApp's order, different people in one claim, two claims
at once never on the same connection; the lease renewed before each message, and a process that
lost it stops; every message checks the connection again, and a refused one gets "not connected"
once and ends; a message past WhatsApp's 24 hours expires unanswered; a reply that can't be sent
rests, doubling, keeps the order, and a refusal for good fails it at once; an error while answering
keeps the lease and counts a try; a crash between a reply and its "done" sends that reply once
more, no more; nothing logged names a body or a number. And the process loop
(``channel_main.run``). W3: the super admin's switches and Management's tick are checked too; STOP,
Yes to "Disconnect?" and [Not me] on the connection's own welcome end it and say so once."""

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import Connection, text

from kaagaz import channel_main
from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.channel.loop import (
    ASK_DISCONNECT,
    DISCONNECTED,
    MAX_TRIES,
    NOT_CONNECTED,
    STILL_CONNECTED,
    Channel,
)
from kaagaz.db import whatsapp as wa
from kaagaz.db.engine import Database
from kaagaz.db.whatsapp import Claim, Linked
from kaagaz.ingestion.errors import BackendUnavailable
from tests.test_api_support import Api
from tests.whatsapp_fake import (
    NUMBER_KEY,
    PLACEHOLDER,
    Echo,
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
STOLEN = datetime(2099, 1, 1, tzinfo=UTC)  # another process's lease


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def _owner(db: Database, statement: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return [tuple(row) for row in result] if result.returns_rows else []


class _Sender:
    """MetaWhatsApp, with ``before`` called ahead of each send (to raise, or to look around)."""

    def __init__(self, real: MetaWhatsApp, before: Callable[[], None]) -> None:
        self.real, self.before = real, before

    def send_text(self, to: str, body: str) -> None:
        self.before()
        self.real.send_text(to, body)

    def send_buttons(self, to: str, body: str, buttons: Any) -> None:
        self.before()
        self.real.send_buttons(to, body, buttons)

    def send_list(self, to: str, body: str, button: str, rows: Any) -> None:
        self.before()
        self.real.send_list(to, body, button, rows)

    def typing(self, message_id: str) -> None:  # W5: not a reply, so not one ``before`` is for
        self.real.typing(message_id)


class _World:
    """An API on the fake WhatsApp, its channel in the API's role, and connected people."""

    def __init__(self, db: Database, fake: WhatsAppFake, before: Callable[[], None] | None = None) -> None:
        self.db, self.fake = db, fake
        self.api = Api(db, settings=settings_for(fake))
        self.client = self.api.client()
        sender = MetaWhatsApp.from_settings(self.api.settings)
        self.channel = Channel(
            db=self.api.app_db,
            sender=sender if before is None else _Sender(sender, before),
            number_key=NUMBER_KEY,
            clock=self.api.clock,
            chat=Echo(),
        )

    def person(
        self, customer: str | None = None, role: str = "member", *, legacy: bool = False
    ) -> tuple[str, str, str, str]:
        """(customer, person, number, link) of a newly connected person, on the company sign-in
        (``legacy``: still on Kaagaz's old sign-in, which no longer may use WhatsApp)."""
        if customer is None:
            customer, _ = self.api.customer()
        person, _ = self.api.add_user(customer, role)
        if not legacy:
            _owner(
                self.db,
                "UPDATE users SET auth_authority = 'keycloak' WHERE customer_id = :c AND id = :u",
                {"c": customer, "u": person},
            )
        number = new_number()
        return customer, person, number, connect(self.db, customer, person, number)

    def send(self, number: str, *messages: dict[str, Any]) -> None:
        assert post(self.client, delivery(*messages)).status_code == 200

    def run(self) -> list[Linked]:
        """Claim and handle until nothing is waiting (other tests' strays included)."""
        handled: list[Linked] = []
        while claimed := self.channel.claim(100):
            for claim in claimed:
                self.channel.handle(claim)
            handled += [claim.linked for claim in claimed]
        return handled

    def lease(self, customer: str, person: str, link: str) -> Claim:
        """The claim a process would hold on this connection now (its lease run out or freed)."""
        until = _owner(
            self.db,
            "UPDATE whatsapp_links SET busy_until = now() + interval '240 seconds' WHERE id = :l"
            " RETURNING busy_until",
            {"l": link},
        )[0][0]
        return Claim(Linked(customer, person, link), until)

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

    def lease_left(self, link: str) -> float:
        """Seconds until the connection's lease runs out (negative or 0: free)."""
        row = _owner(
            self.db,
            "SELECT coalesce(extract(epoch FROM busy_until - now()), 0)::float FROM whatsapp_links"
            " WHERE id = :l",
            {"l": link},
        )
        return float(row[0][0])


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


def test_a_person_not_on_the_company_sign_in_loses_their_whatsapp_channel(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    customer, user, number, link = world.person(legacy=True)
    world.send(number, message(number, world.api.clock(), "show files", wamid="wamid.mfa"))

    world.run()

    assert world.texts_to(number) == [NOT_CONNECTED]
    assert world.link(link)[0] == "not_allowed"
    assert world.messages(link) == [("wamid.mfa", "done", True, True, 0)]
    with db.user(customer, user) as conn:
        assert wa.mine(conn, customer, user) is None


def test_a_person_is_handled_by_one_process_at_a_time_and_different_people_together(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    a = world.person()
    b = world.person()
    for _, _, number, _ in (a, b):
        world.send(number, message(number, world.api.clock()))
    first = {claim.linked for claim in world.channel.claim(100)}
    assert {Linked(*a[:2], a[3]), Linked(*b[:2], b[3])} <= first  # side by side, in one claim
    assert Linked(*a[:2], a[3]) not in {c.linked for c in world.channel.claim(100)}  # leased: no second
    _owner(
        db, "UPDATE whatsapp_links SET busy_until = now() - interval '1 second' WHERE id = :l", {"l": a[3]}
    )
    assert Linked(*a[:2], a[3]) in {c.linked for c in world.channel.claim(100)}  # a crashed one's ran out
    # Nobody handled these claims: free both leases, as running out would, and answer.
    free = "UPDATE whatsapp_links SET busy_until = NULL WHERE id = ANY(CAST(:l AS uuid[]))"
    _owner(db, free, {"l": [a[3], b[3]]})
    world.run()
    assert world.texts_to(a[2]) == world.texts_to(b[2]) == [PLACEHOLDER]


def test_two_claims_at_once_never_take_the_same_connection(db: Database, fake: WhatsAppFake) -> None:
    """#603 review: two processes claim at the same moment, on their own connections; the second
    skips what the first holds, without waiting for it."""
    world = _World(db, fake)
    ours = set()
    for _ in range(2):
        _, _, number, link = world.person()
        world.send(number, message(number, world.api.clock()))
        ours.add(link)
    claim = text("SELECT link_id::text FROM kaagaz_whatsapp_claim(100, 60)")
    with world.api.app_db.system() as first:
        took = {row[0] for row in first.execute(claim)}
        with world.api.app_db.system() as second:  # the first has not committed yet
            second.execute(text("SET LOCAL lock_timeout = '2s'"))
            also = {row[0] for row in second.execute(claim)}
    assert ours <= took and not ours & also


def _refuse(world: _World, how: str, customer: str, person: str) -> None:
    settings = "UPDATE whatsapp_org_settings SET {} WHERE customer_id = :c"
    owner = {
        "disabled": "UPDATE users SET disabled = true WHERE id = :u",
        "signed_out_everywhere": "UPDATE users SET session_version = session_version + 1 WHERE id = :u",
        "whatsapp_off": settings.format("enabled = false"),
        "role_not_ticked": settings.format("roles = ARRAY['reviewer']"),
        "organisation_disabled": "UPDATE customers SET status = 'disabled', disabled_at = now()"
        " WHERE id = :c",
        # W3: the super admin's switch for the organisation, and Management without its extra tick.
        "super_admin_switch_off": "INSERT INTO org_feature_switches (customer_id, feature, enabled)"
        " VALUES (:c, 'whatsapp', false)",
        "management_not_ticked": "UPDATE users SET management = true WHERE id = :u",
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
        ("super_admin_switch_off", "not_allowed"),  # Changed on purpose (W3)
        ("management_not_ticked", "not_allowed"),  # Changed on purpose (W3)
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
        "SELECT subject_user_id::text, details->>'reason', details->>'number_last4' FROM audit_log"
        " WHERE customer_id = :c AND action = 'whatsapp.disconnected'",
        {"c": customer},
    )
    assert audit == [(person, reason, number[-4:])]
    world.send(number, message(number, world.api.clock(), "three", wamid=f"wamid.{how}.3"))
    assert len(world.messages(link)) == 2  # no longer connected: the webhook stores nothing


def _ended(world: _World, link: str) -> list[tuple[Any, ...]]:
    return _owner(
        world.db,
        "SELECT end_reason, actor_user_id::text FROM whatsapp_links l LEFT JOIN audit_log a"
        " ON a.customer_id = l.customer_id AND a.action = 'whatsapp.disconnected'"
        " AND a.details->>'link_id' = l.id::text WHERE l.id = :l",
        {"l": link},
    )


def _tap(number: str, sent_at: datetime, kind: str, value: str) -> dict[str, Any]:
    """A tapped template button (kind "button", its payload) or reply button ("interactive", its id)."""
    tapped = {**message(number, sent_at), "type": kind}
    del tapped["text"]
    if kind == "button":
        tapped["button"] = {"payload": value, "text": "Not me"}
    else:
        tapped["interactive"] = {"type": "button_reply", "button_reply": {"id": value, "title": "x"}}
    return tapped


@pytest.mark.parametrize("word", ["STOP", " stop "])
def test_stop_ends_the_connection_and_says_so_once(db: Database, fake: WhatsAppFake, word: str) -> None:
    world = _World(db, fake)
    _, person, number, link = world.person()
    now = world.api.clock()
    world.send(number, message(number, now - timedelta(seconds=1), word), message(number, now, "after"))
    world.run()
    assert _ended(world, link) == [("person", person)]  # audited, the person its actor
    assert world.texts_to(number) == [DISCONNECTED]  # nothing for "after": no longer connected
    assert world.link(link)[:2] == ("person", True)


def test_not_me_on_this_connections_own_welcome_ends_it_and_an_old_welcomes_does_not(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    _, person, number, link = world.person()
    now = world.api.clock()
    world.send(number, _tap(number, now, "button", f"not_me:{uuid.uuid4()}"))  # an older connection's
    world.run()
    assert world.link(link)[0] is None and world.texts_to(number) == [PLACEHOLDER]
    world.send(number, _tap(number, now, "button", f"not_me:{link}"))
    world.run()
    assert _ended(world, link) == [("not_me", person)]
    assert world.texts_to(number) == [PLACEHOLDER, DISCONNECTED]


def test_disconnect_asks_first_and_ends_only_on_yes(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    _, person, number, link = world.person()
    now = world.api.clock()
    world.send(number, message(number, now, "Disconnect"))
    world.run()
    assert [what for _, what in fake.sent()] == [(ASK_DISCONNECT, ["disconnect_yes", "keep"])]
    world.send(number, _tap(number, now, "interactive", "keep"))
    world.run()
    assert world.link(link)[0] is None and world.texts_to(number) == [STILL_CONNECTED]
    world.send(number, _tap(number, now, "interactive", "disconnect_yes"))
    world.run()
    assert _ended(world, link) == [("person", person)]
    assert world.texts_to(number) == [STILL_CONNECTED, DISCONNECTED]


def test_a_message_past_whatsapps_24_hours_expires_unanswered(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    _, _, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.late"))
    world.api.clock.advance(24 * 3600 + 1)
    world.run()
    assert world.texts_to(number) == []
    assert world.messages(link) == [("wamid.late", "expired", True, False, 0)]


def test_a_reply_not_sent_rests_doubling_keeps_the_order_and_a_refusal_for_good_fails_at_once(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    now = world.api.clock()
    world.send(
        number,
        message(number, now - timedelta(seconds=1), wamid="wamid.a"),
        message(number, now, wamid="wamid.b"),
    )
    # Past MAX_TRIES and up to the cap: a reply not sent is tried until its 24 hours end.
    rests = (
        (500, 1, 30),
        (503, 2, 60),
        (429, 3, 120),
        (500, 4, 240),
        (502, 5, 480),
        (500, 6, 600),
        (0, 7, 600),
    )
    for failure, tries, rest in rests:
        fake.failures = [failure]
        world.channel.handle(world.lease(customer, person, link))
        assert world.messages(link) == [
            ("wamid.a", "waiting", False, False, tries),
            ("wamid.b", "waiting", False, False, 0),  # never ahead of a
        ]
        assert rest - 5 < world.lease_left(link) <= rest  # resting, the lease kept that long
    assert Linked(customer, person, link) not in world.run()
    assert world.texts_to(number) == []
    fake.failures = [400]  # Meta refuses it for good: failed at once, and b goes on
    with caplog.at_level(logging.ERROR, logger="kaagaz.channel"):
        world.channel.handle(world.lease(customer, person, link))
    assert world.messages(link) == [("wamid.b", "done", True, True, 0), ("wamid.a", "failed", True, False, 8)]
    assert world.texts_to(number) == [PLACEHOLDER]
    assert [
        (r.getMessage(), getattr(r, "code", None)) for r in caplog.records if r.levelno >= logging.ERROR
    ] == [("whatsapp message failed", "refused:400")]


def test_a_refused_token_is_logged_at_error_and_the_reply_tried_again(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.auth"))
    fake.failures = [401]
    with caplog.at_level(logging.ERROR, logger="kaagaz.channel"):
        world.channel.handle(world.lease(customer, person, link))
    assert [getattr(r, "code", None) for r in caplog.records if r.levelno >= logging.ERROR] == [
        "whatsapp_auth"
    ]
    assert world.messages(link) == [("wamid.auth", "waiting", False, False, 1)]
    world.channel.handle(world.lease(customer, person, link))
    assert world.texts_to(number) == [PLACEHOLDER]


def test_an_error_while_answering_keeps_the_lease_so_nothing_is_sent_again_at_once(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    """#603 review: the lease is not freed on an error, so no other turn sends at once; each error
    counts a try, and MAX_TRIES of them fail the message (logged)."""

    def broken() -> None:
        raise RuntimeError("a bug while answering")

    world = _World(db, fake, before=broken)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.bug"))
    claim = world.lease(customer, person, link)
    with pytest.raises(RuntimeError):
        world.channel.handle(claim)
    assert world.lease_left(link) > 200  # the renewed lease, still held
    assert Linked(customer, person, link) not in world.run()
    assert world.messages(link) == [("wamid.bug", "waiting", False, False, 1)]
    with caplog.at_level(logging.ERROR, logger="kaagaz.channel"):
        for _ in range(MAX_TRIES - 1):
            with pytest.raises(RuntimeError):
                world.channel.handle(world.lease(customer, person, link))
    assert world.messages(link) == [("wamid.bug", "failed", True, False, MAX_TRIES)]
    assert [
        (r.getMessage(), getattr(r, "code", None)) for r in caplog.records if r.levelno >= logging.ERROR
    ] == [("whatsapp message failed", "RuntimeError")]
    assert world.texts_to(number) == []


def test_a_long_run_of_one_persons_messages_keeps_its_lease(db: Database, fake: WhatsAppFake) -> None:
    """#603 review: the lease is renewed before each message, so one person's long run never runs
    past it; and once another process holds it (it ran out), this one stops and leaves it alone."""
    leases: list[datetime] = []

    def look() -> None:
        leases.append(_owner(db, "SELECT busy_until FROM whatsapp_links WHERE id = :l", {"l": link})[0][0])
        if len(leases) == 2:  # another process took it while this one answered
            _owner(db, "UPDATE whatsapp_links SET busy_until = :t WHERE id = :l", {"l": link, "t": STOLEN})

    world = _World(db, fake, before=look)
    customer, person, number, link = world.person()
    now = world.api.clock()
    world.send(
        number, *(message(number, now + timedelta(seconds=i), wamid=f"wamid.run.{i}") for i in range(3))
    )
    claim = world.lease(customer, person, link)
    world.channel.handle(claim)
    assert claim.until < leases[0] < leases[1]  # renewed before each message
    assert world.texts_to(number) == [PLACEHOLDER, PLACEHOLDER]
    assert [row[:2] for row in world.messages(link)] == [
        ("wamid.run.0", "done"),
        ("wamid.run.1", "done"),
        ("wamid.run.2", "waiting"),  # not this process's any more
    ]
    held = _owner(db, "SELECT busy_until FROM whatsapp_links WHERE id = :l", {"l": link})[0][0]
    assert held == STOLEN  # the other process's lease, left as it was


def test_a_process_that_lost_its_lease_while_sending_leaves_the_new_one(
    db: Database, fake: WhatsAppFake
) -> None:
    """#603 review: a reply not sent rests the connection, but only on the lease this process holds;
    one another process took meanwhile is left as it is."""

    def steal() -> None:
        _owner(db, "UPDATE whatsapp_links SET busy_until = :t WHERE id = :l", {"l": link, "t": STOLEN})

    world = _World(db, fake, before=steal)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.stolen"))
    fake.failures = [500]
    world.channel.handle(world.lease(customer, person, link))
    held = _owner(db, "SELECT busy_until FROM whatsapp_links WHERE id = :l", {"l": link})[0][0]
    assert held == STOLEN
    assert world.messages(link) == [("wamid.stolen", "waiting", False, False, 1)]


def test_a_crash_between_a_reply_and_its_done_sends_that_reply_once_more_only(
    db: Database, fake: WhatsAppFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _World(db, fake)
    customer, person, number, link = world.person()
    world.send(number, message(number, world.api.clock(), wamid="wamid.crash"))
    finish = wa.finish

    def crash(conn: Connection, linked: Linked, message_id: str, state: str, *, replied: bool) -> None:
        raise RuntimeError("the process died after sending")

    monkeypatch.setattr(wa, "finish", crash)
    with pytest.raises(RuntimeError):
        world.channel.handle(world.lease(customer, person, link))
    monkeypatch.setattr(wa, "finish", finish)
    assert world.texts_to(number) == [PLACEHOLDER]
    assert world.messages(link) == [("wamid.crash", "waiting", False, False, 1)]
    _owner(
        db, "UPDATE whatsapp_links SET busy_until = now() - interval '1 second' WHERE id = :l", {"l": link}
    )
    world.run()  # its lease ran out: another turn answers it again, and marks it done
    world.run()
    assert world.texts_to(number) == [PLACEHOLDER, PLACEHOLDER]
    assert world.messages(link) == [("wamid.crash", "done", True, True, 1)]


def test_nothing_logged_names_a_body_or_a_number(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    """The webhook, the channel and the adapter, through a drop, a reply not sent and a refusal."""
    world = _World(db, fake)
    customer, person, number, link = world.person()
    secret = "SECRET-BODY-Rs-98765"
    digits = number.removeprefix("+")
    with caplog.at_level(logging.DEBUG):
        world.send(number, message(number, world.api.clock() - timedelta(days=2), secret, wamid="wamid.old"))
        world.send(number, message(number, world.api.clock(), secret, wamid="wamid.secret"))
        fake.failures = [500]
        world.channel.handle(world.lease(customer, person, link))
        world.channel.handle(world.lease(customer, person, link))
        _refuse(world, "disabled", customer, person)
        world.send(number, message(number, world.api.clock(), secret, wamid="wamid.refused"))
        world.run()
    assert {"too_old", "refused:500"} <= {getattr(r, "code", None) for r in caplog.records}
    for record in caplog.records:
        shown = record.getMessage() + repr(record.__dict__)
        assert secret not in shown and digits not in shown, record.name


class _Loop:
    """A channel for ``channel_main.run``: the claims it is given, then nothing; records the rest."""

    def __init__(self, claims: list[list[Claim] | Exception], *, sweep_fails: bool = False) -> None:
        self.claims = claims
        self.handled: list[Claim] = []
        self.sweeps = 0
        self.revived = 0
        self.sweep_fails = sweep_fails
        self.done = threading.Event()

    def claim(self, limit: int) -> list[Claim]:
        if not self.claims:
            self.done.set()
            return []
        claim = self.claims.pop(0)
        if isinstance(claim, Exception):
            raise claim
        return claim

    def handle(self, claim: Claim) -> None:
        self.handled.append(claim)
        if claim.linked.link_id == "boom":
            raise RuntimeError("one person's failure")

    def sweep(self) -> int:
        self.sweeps += 1
        if self.sweep_fails:
            raise RuntimeError("the sweep failed")
        return 0

    def revive(self) -> int:  # W5: no notices put aside here
        self.revived += 1
        return 0


def _run(loop: _Loop) -> None:
    stop = threading.Event()
    running = threading.Thread(target=channel_main.run, args=(cast(Channel, loop), stop, 2))
    running.start()
    assert loop.done.wait(10)
    stop.set()
    running.join(10)
    assert not running.is_alive()


def _claims(*links: str) -> list[Claim]:
    return [Claim(Linked("c", "u", link), STOLEN) for link in links]


def test_the_loop_sweeps_claims_and_handles_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_main, "PAUSE_SECONDS", 0.01)
    monkeypatch.setattr(channel_main, "POLL_SECONDS", 0.01)
    loop = _Loop([_claims("one", "boom"), BackendUnavailable("database"), _claims("two")])
    _run(loop)
    handled = sorted(claim.linked.link_id for claim in loop.handled)
    assert handled == ["boom", "one", "two"]  # one failure stops nothing
    assert loop.sweeps == 1  # at start, then hourly
    assert loop.revived >= 3  # W5: every turn, before the claims


def test_a_sweep_that_fails_never_stops_the_claims(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(channel_main, "POLL_SECONDS", 0.01)
    loop = _Loop([_claims("one")], sweep_fails=True)
    with caplog.at_level(logging.ERROR, logger="kaagaz.channel"):
        _run(loop)
    assert [claim.linked.link_id for claim in loop.handled] == ["one"]
    assert loop.sweeps == 1  # tried again only after SWEEP_SECONDS
    assert [
        (r.getMessage(), getattr(r, "code", None)) for r in caplog.records if r.levelno >= logging.ERROR
    ] == [("whatsapp sweep failed", "RuntimeError")]
