"""Connecting a WhatsApp number (#603, PRD 7 section 15 W3), on the fake WhatsApp. The bot messages
first: the proof (the password, and the code with two-step on; Google for one who signs in with Google
only), the number and the tick, Meta's code template to that number, the code typed back (once, for
10 minutes, 5 tries, only the newest, only for its own number), at most 3 codes an hour an account and
5 a day a number; "already connected" only after the right code, naming no one; the welcome, the audit
row and the security email. The second way ("Link <code>" from the phone, then "Connect?" on the
screen). Disconnect. A number not connected told once a day, BLOCK, and WhatsApp's owner change. The
Admin's switches (the password to turn it on, the roles and the extra ticks, audited, emailed), who is
connected, and the Admin's Disconnect."""

import threading
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.api.routers.whatsapp import GO_BACK
from kaagaz.api.routers.whatsapp_account import NUMBER_TAKEN
from kaagaz.channel.loop import NOT_CONNECTED
from kaagaz.channel.numbers import number_hash
from kaagaz.db import google_rules
from kaagaz.db import whatsapp as wa
from kaagaz.db.engine import Database
from tests.oidc_fake import FakeGoogle, google_settings, use_google
from tests.test_api_support import CSRF, PASSWORD, Api, Signed, call, wrong_code
from tests.whatsapp_fake import (
    BUSINESS_NUMBER,
    NUMBER_KEY,
    WhatsAppFake,
    connect,
    delivery,
    message,
    new_number,
    post,
    running_fake,
    whatsapp_settings,
)

pytestmark = pytest.mark.db
SITE = "https://kaagaz.example.com"


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def _owner(db: Database, statement: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params or {})
        return [tuple(row) for row in result] if result.returns_rows else []


def _on(
    db: Database, customer: str, roles: tuple[str, ...] = ("member", "reviewer"), management: bool = False
) -> None:
    """WhatsApp on for the organisation, as its Admin's switch leaves it."""
    _owner(
        db,
        "INSERT INTO whatsapp_org_settings (customer_id, enabled, roles, management)"
        " VALUES (:c, true, :r, :m)"
        " ON CONFLICT (customer_id) DO UPDATE SET enabled = true, roles = EXCLUDED.roles,"
        " management = EXCLUDED.management",
        {"c": customer, "r": list(roles), "m": management},
    )


class _World:
    def __init__(self, db: Database, fake: WhatsAppFake, mailer: MemoryMailer | None = None, **settings: Any):
        self.db, self.fake, self.mailer = db, fake, mailer
        if mailer is not None:
            settings |= {"email_enabled": True, "email_from": "no-reply@example.com", "app_url": SITE}
        self.api = Api(db, settings=whatsapp_settings(fake, **settings), mailer=mailer)

    def person(self, role: str = "member", customer: str | None = None, *, on: bool = True) -> Signed:
        if customer is None:
            customer, _ = self.api.customer("Shree Ganesh CA Associates")
            if on:
                _on(self.db, customer)
        return self.api.sign_in(customer, role)

    def prove(self, signed: Signed, password: str = PASSWORD) -> httpx.Response:
        body: dict[str, str] = {"password": password}
        if signed.totp_secret is not None:
            self.api.clock.advance(30)  # a fresh code: the sign-in used this step's
            body["code"] = self.api.code(signed.totp_secret)
        return signed.client.post("/api/whatsapp/prove", json=body, headers=CSRF)

    def ask(self, signed: Signed, number: str, way: str = "sent", agreed: bool = True) -> httpx.Response:
        body = {"number": number, "agreed": agreed, "way": way}
        return signed.client.post("/api/whatsapp/codes", json=body, headers=CSRF)

    def proved_ask(self, signed: Signed, number: str, way: str = "sent") -> httpx.Response:
        assert self.prove(signed).status_code == 200
        return self.ask(signed, number, way)

    def verify(self, signed: Signed, code: str) -> httpx.Response:
        return signed.client.post("/api/whatsapp/codes/verify", json={"code": code}, headers=CSRF)

    def connected(self, signed: Signed) -> tuple[str, str]:
        """A number connected to the person through the screen: (number, link)."""
        number = new_number()
        assert self.proved_ask(signed, number).json()["sent"] is True
        assert self.verify(signed, self.fake.code_to(number)).status_code == 200
        return number, self.link_of(number)

    def link_of(self, number: str) -> str:
        with self.api.app_db.system() as conn:
            found = wa.lookup(conn, number_hash(NUMBER_KEY, number))
        assert found is not None
        return found.link_id

    def live(self, number: str) -> bool:
        with self.api.app_db.system() as conn:
            return wa.lookup(conn, number_hash(NUMBER_KEY, number)) is not None

    def me(self, signed: Signed) -> dict[str, Any]:
        reply = signed.client.get("/api/whatsapp/me")
        assert reply.status_code == 200, reply.text
        return dict(reply.json())

    def to(self, number: str) -> list[Any]:
        return [what for to, what in self.fake.sent() if to == number[1:]]

    def from_phone(self, number: str, body: str) -> None:
        assert post(self.api.client(), delivery(message(number, self.api.clock(), body))).status_code == 200


def _error(reply: httpx.Response) -> tuple[int, str, str]:
    return reply.status_code, reply.json()["error"]["code"], reply.json()["error"]["message"]


def _ended(db: Database, link: str) -> tuple[Any, ...]:
    return _owner(db, "SELECT end_reason FROM whatsapp_links WHERE id = :l", {"l": link})[0]


# --- connecting ------------------------------------------------------------------------------------


def test_connecting_a_number_the_bot_messaging_first(db: Database, fake: WhatsAppFake) -> None:
    mailer = MemoryMailer()
    world = _World(db, fake, mailer)
    member = world.person()
    assert member.client.get("/api/auth/me").json()["whatsapp"] is True
    assert world.me(member) == {"state": "none", "google": False, "totp": False}
    number = new_number()
    typed = f"{number[:2]} {number[2:5]}-{number[5:]}"  # as people type it
    asked = world.proved_ask(member, typed)
    assert asked.json() == {"last4": number[-4:], "sent": True}
    code = world.fake.code_to(number)
    assert world.to(number) == [("kaagaz_connect_code", [code, code])]
    connected = world.verify(member, f" {code} ")
    assert connected.status_code == 200, connected.text
    link = world.link_of(number)
    assert connected.json()["state"] == "connected" and connected.json()["last4"] == number[-4:]
    assert world.to(number)[1] == (
        "kaagaz_welcome",
        [
            "Test member",
            f"m•••@{member.email.split('@')[1]}",
            "Shree Ganesh CA Associates",
            "start",
            f"not_me:{link}",
        ],
    )
    audited = _owner(
        db,
        "SELECT actor_user_id::text, subject_user_id::text, details FROM audit_log"
        " WHERE customer_id = :c AND action = 'whatsapp.connected'",
        {"c": member.customer_id},
    )
    assert audited == [(member.user_id, member.user_id, {"link_id": link, "number_last4": number[-4:]})]
    version = _owner(db, "SELECT session_version FROM users WHERE id = :u", {"u": member.user_id})[0][0]
    assert _owner(
        db, "SELECT session_version, phone_last4 FROM whatsapp_links WHERE id = :l", {"l": link}
    ) == [(version, number[-4:])]
    assert [(sent.to, sent.subject) for sent in mailer.sent] == [
        (member.email, "WhatsApp was connected to your Kaagaz account")
    ]
    # Its messages now reach the person's channel; the code works once.
    assert world.verify(member, code).status_code == 400


def test_a_code_needs_the_proof_the_tick_and_a_number(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    assert _error(world.ask(member, new_number()))[:2] == (403, "proof_needed")
    wrong = world.prove(member, password="not the password!!")
    assert _error(wrong) == (400, "bad_request", "Your current password is not right.")
    assert world.api.user_state(member.customer_id, member.user_id)["failed_logins"] == 1
    assert world.prove(member).status_code == 200
    assert world.api.user_state(member.customer_id, member.user_id)["failed_logins"] == 0
    assert _error(world.ask(member, new_number(), agreed=False))[0] == 400
    for bad in ("12", "+0 555 1234 567", "phone"):
        assert _error(world.ask(member, bad))[0] == 400
    # The proof is this person's, for 10 minutes.
    other = world.person()
    other.client.cookies.set(
        "kaagaz_whatsapp_proof", member.client.cookies.get("kaagaz_whatsapp_proof") or ""
    )
    assert _error(world.ask(other, new_number()))[:2] == (403, "proof_needed")
    world.api.clock.advance(10 * 60 + 1)
    assert _error(world.ask(member, new_number()))[:2] == (403, "proof_needed")
    assert fake.sent() == []


def test_two_step_people_give_the_code_too(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _on(db, admin.customer_id, roles=("member", "reviewer", "admin"))
    assert admin.totp_secret is not None
    no_code = admin.client.post("/api/whatsapp/prove", json={"password": PASSWORD}, headers=CSRF)
    assert _error(no_code)[0] == 400
    wrong = {"password": PASSWORD, "code": wrong_code(world.api, admin.totp_secret)}
    assert _error(admin.client.post("/api/whatsapp/prove", json=wrong, headers=CSRF))[0] == 400
    assert world.proved_ask(admin, new_number()).json()["sent"] is True


@pytest.mark.parametrize(
    ("role", "roles", "management", "is_management", "refused"),
    [
        ("member", None, False, False, "off"),  # the Admin never turned it on
        ("reviewer", ("member",), False, False, "not_allowed"),
        ("admin", ("member", "reviewer"), False, False, "not_allowed"),  # Admins need their tick
        ("admin", ("member", "reviewer", "admin"), False, False, None),
        ("member", ("member", "reviewer"), False, True, "not_allowed"),  # Management needs its tick
        ("member", ("member", "reviewer"), True, True, None),
    ],
)
def test_whatsapp_off_or_a_role_not_ticked_gets_no_code(
    db: Database,
    fake: WhatsAppFake,
    role: str,
    roles: tuple[str, ...] | None,
    management: bool,
    is_management: bool,
    refused: str | None,
) -> None:
    world = _World(db, fake)
    customer, _ = world.api.customer()
    if roles is not None:
        _on(db, customer, roles, management)
    signed = world.person(role, customer)
    if is_management:
        _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": signed.user_id})
    assert world.me(signed)["state"] == (refused or "none")
    reply = world.proved_ask(signed, new_number())
    if refused is None:
        assert reply.json()["sent"] is True
    else:
        assert _error(reply)[:2] == (403, refused)
        assert fake.sent() == []


def test_the_super_admins_switch_turns_it_off_for_the_organisation(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    _owner(
        db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, 'whatsapp', false)",
        {"c": member.customer_id},
    )
    assert world.me(member)["state"] == "off"
    assert _error(world.proved_ask(member, new_number()))[:2] == (403, "off")


def test_a_code_works_once_for_ten_minutes_with_five_tries(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    world.proved_ask(member, number)
    right = world.fake.code_to(number)
    wrong = "000000" if right != "000000" else "111111"
    messages = [_error(world.verify(member, wrong))[2] for _ in range(5)]
    assert messages == [
        "That code isn't right. 4 tries left.",
        "That code isn't right. 3 tries left.",
        "That code isn't right. 2 tries left.",
        "That code isn't right. 1 try left.",
        "That code isn't right. Ask for a new one.",
    ]
    assert _error(world.verify(member, right))[2] == "Too many wrong codes. Ask for a new one."
    world.ask(member, number)  # a new code, made to run out
    _owner(db, "UPDATE whatsapp_link_codes SET created_at = created_at - interval '11 minutes',"
               " expires_at = expires_at - interval '11 minutes' WHERE user_id = :u",
           {"u": member.user_id})  # fmt: skip
    assert (
        _error(world.verify(member, world.fake.code_to(number)))[2]
        == "That code has expired. Ask for a new one."
    )
    assert not world.live(number)
    world.ask(member, number)
    assert world.verify(member, world.fake.code_to(number)).status_code == 200
    assert world.live(number)


def test_only_the_newest_code_counts_and_only_for_the_number_it_went_to(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    member = world.person()
    first, second = new_number(), new_number()
    world.proved_ask(member, first)
    world.ask(member, second)
    assert _error(world.verify(member, world.fake.code_to(first)))[1] == "wrong_code"
    assert world.verify(member, world.fake.code_to(second)).json()["last4"] == second[-4:]
    assert world.live(second) and not world.live(first)


def test_at_most_three_codes_an_hour_an_account_and_five_a_day_a_number(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    member = world.person()
    assert world.prove(member).status_code == 200
    for _ in range(3):
        assert world.ask(member, new_number()).status_code == 200
    assert _error(world.ask(member, new_number())) == (
        429,
        "rate_limited",
        "Too many codes were asked for. Try again in an hour.",
    )
    _owner(db, "UPDATE whatsapp_link_codes SET created_at = created_at - interval '61 minutes',"
           " expires_at = expires_at - interval '61 minutes' WHERE user_id = :u",
           {"u": member.user_id})  # fmt: skip
    assert world.ask(member, new_number()).status_code == 200  # an hour on
    number = new_number()
    for _ in range(5):  # five people of five organisations
        assert world.proved_ask(world.person(), number).status_code == 200
    assert _error(world.proved_ask(world.person(), number)) == (
        429,
        "rate_limited",
        "Too many codes went to this number today. Try again tomorrow.",
    )
    assert len(world.to(number)) == 5


def test_already_connected_elsewhere_is_said_only_after_the_right_code_and_names_no_one(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    owner_customer, _ = world.api.customer("Sharma Traders")
    owner, _ = world.api.add_user(owner_customer, "member")
    number = new_number()
    theirs = connect(db, owner_customer, owner, number)
    member = world.person()
    asked = world.proved_ask(member, number)
    assert asked.json() == {"last4": number[-4:], "sent": True}  # as for any number
    code = world.fake.code_to(number)
    wrong = "000000" if code != "000000" else "111111"
    assert _error(world.verify(member, wrong))[:2] == (400, "wrong_code")
    taken = world.verify(member, code)
    assert _error(taken) == (409, "number_taken", NUMBER_TAKEN)
    assert "Sharma" not in taken.text and owner not in taken.text
    assert _error(world.verify(member, code))[0] == 400  # spent
    assert world.link_of(number) == theirs and world.me(member)["state"] == "none"


def test_connecting_again_ends_the_persons_own_connection_first(db: Database, fake: WhatsAppFake) -> None:
    """Item 19: the person's live connection ends before the new one, a stale one too (its hash made
    with a WHATSAPP_NUMBER_KEY since changed)."""
    world = _World(db, fake)
    member = world.person()
    number, link = world.connected(member)
    _owner(db, "UPDATE whatsapp_links SET phone_hash = :h WHERE id = :l",
           {"h": number_hash("an old key " + "x" * 30, number), "l": link})  # fmt: skip
    world.api.clock.advance(11 * 60)  # a new proof
    world.proved_ask(member, number)
    assert world.verify(member, world.fake.code_to(number)).status_code == 200
    assert _ended(db, link) == ("person",) and world.link_of(number) != link
    other = new_number()
    newer = world.link_of(number)
    world.ask(member, other)
    assert world.verify(member, world.fake.code_to(other)).status_code == 200
    assert _ended(db, newer) == ("person",) and world.live(other) and not world.live(number)


def test_disconnect_from_the_account_page(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    number, link = world.connected(member)
    me = world.me(member)
    assert (me["state"], me["last4"], me["last_used_at"]) == ("connected", number[-4:], None)
    reply = member.client.post("/api/whatsapp/disconnect", headers=CSRF)
    assert reply.json()["state"] == "none" and _ended(db, link) == ("person",)
    assert _error(member.client.post("/api/whatsapp/disconnect", headers=CSRF))[0] == 404


# --- the second way ---------------------------------------------------------------------------------


def _phone_state(signed: Signed) -> str:
    return str(signed.client.get("/api/whatsapp/codes/phone").json()["state"])


def _phone_confirm(signed: Signed) -> httpx.Response:
    return signed.client.post("/api/whatsapp/codes/phone/confirm", headers=CSRF)


def _other(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def _second_way_connects(world: _World, member: Signed, number: str, shown: dict[str, Any]) -> None:
    """The screen's own steps once it shows the second way: the code and its link, "Link <code>" from
    the phone (a wrong one, and the right one from another number, change nothing), then Connect."""
    code = shown["code"]
    assert shown == {
        "last4": number[-4:],
        "sent": False,
        "code": code,
        "link": f"https://wa.me/{BUSINESS_NUMBER}?text=Link%20{code}",
    }
    assert _phone_state(member) == "waiting" and _error(_phone_confirm(member))[:2] == (409, "not_confirmed")
    world.from_phone(new_number(), f"Link {code}")  # the right code from another number
    world.from_phone(number, f"Link {_other(code)}")  # one wrong try
    assert _phone_state(member) == "waiting"
    world.from_phone(number, f"  link {code} ")
    assert _phone_state(member) == "confirmed" and world.to(number)[-1] == GO_BACK
    connected = _phone_confirm(member)
    assert connected.json()["state"] == "connected" and world.live(number)
    link = world.link_of(number)
    # Inside the 24 hours the phone just opened: the welcome as a text with the same buttons.
    body, buttons = world.to(number)[-1]
    assert body.startswith("Connected to Kaagaz as Test member (m•••@example.test) at ")
    assert buttons == ["start", f"not_me:{link}"]
    assert _owner(
        world.db, "SELECT count(*) FROM whatsapp_messages WHERE user_id = :u", {"u": member.user_id}
    ) == [(0,)]
    assert _owner(
        world.db, "SELECT count(*) FROM whatsapp_link_codes WHERE user_id = :u", {"u": member.user_id}
    ) == [(0,)]  # spent: every code of theirs, numbers and all
    assert _phone_state(member) == "expired" and _error(_phone_confirm(member))[0] == 400


def test_a_code_meta_refuses_starts_the_second_way_in_the_same_reply(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    fake.failures = [400]
    _second_way_connects(world, member, number, world.proved_ask(member, number).json())


def test_a_number_that_said_block_gets_no_code_and_the_same_reply_as_a_refusal(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    number = new_number()
    world.from_phone(number, "BLOCK")
    member = world.person()
    shown = world.proved_ask(member, number).json()
    assert world.to(number) == []  # never messaged
    counted = "SELECT count(*) FROM whatsapp_link_codes WHERE user_id = :u AND via = 'sent'"
    assert _owner(db, counted, {"u": member.user_id}) == [(1,)]  # counted, as a refused one is
    _second_way_connects(world, member, number, shown)
    blocked = "SELECT blocked_at IS NULL FROM whatsapp_unknown_numbers WHERE phone_hash = :h"
    assert _owner(db, blocked, {"h": number_hash(NUMBER_KEY, number)}) == [(True,)]  # its owner wrote: lifted


def test_didnt_get_it_only_after_the_bots_code_to_the_same_number(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    assert world.prove(member).status_code == 200
    assert _error(world.ask(member, number, "phone"))[:2] == (409, "send_first")  # never first (decision 9)
    assert world.ask(member, number).json()["sent"] is True
    assert _error(world.ask(member, new_number(), "phone"))[:2] == (409, "send_first")  # another number
    _owner(db, "UPDATE whatsapp_link_codes SET created_at = created_at - interval '11 minutes',"
               " expires_at = expires_at - interval '11 minutes' WHERE user_id = :u",
           {"u": member.user_id})  # fmt: skip
    assert _error(world.ask(member, number, "phone"))[:2] == (409, "send_first")  # its 10 minutes are over
    world.ask(member, number)
    shown = world.ask(member, number, "phone").json()
    assert shown["sent"] is False and world.to(number)[-1][0] == "kaagaz_connect_code"
    _second_way_connects(world, member, number, shown)


def test_the_second_ways_code_takes_five_wrong_tries_from_the_number_then_none(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    fake.failures = [400]
    code = world.proved_ask(member, number).json()["code"]
    for wrong in ("000000", "111111", "222222", "333333", "444444", "555555"):
        if wrong != code:
            world.from_phone(number, f"Link {wrong}")
    world.from_phone(number, f"Link {code}")
    assert _phone_state(member) == "waiting"


def test_link_matches_the_number_and_the_code_not_only_the_newest(db: Database, fake: WhatsAppFake) -> None:
    """Two people asked for the second way for one number: each one's code works for its own."""
    world = _World(db, fake)
    number = new_number()
    first, second = world.person(), world.person()
    fake.failures = [400, 400]
    older = world.proved_ask(first, number).json()["code"]
    world.proved_ask(second, number)
    world.from_phone(number, f"Link {older}")
    assert _phone_state(first) == "confirmed" and _phone_state(second) == "waiting"


def test_the_days_five_count_only_codes_the_bot_was_asked_to_send(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    number = new_number()
    fake.failures = [400] * 4  # each of these four makes a phone code too
    for _ in range(5):
        assert world.proved_ask(world.person(), number).status_code == 200
    assert _error(world.proved_ask(world.person(), number))[0] == 429


def test_a_welcome_meta_refuses_ends_the_new_connection(db: Database, fake: WhatsAppFake) -> None:
    """It never fails open: no connection is left that its phone never heard of."""
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    world.proved_ask(member, number)
    fake.failures = [400]  # the welcome
    reply = world.verify(member, world.fake.code_to(number))
    assert _error(reply)[:2] == (502, "welcome_not_sent")
    assert not world.live(number) and world.me(member)["state"] == "none"
    ended = _owner(db, "SELECT end_reason FROM whatsapp_links WHERE user_id = :u", {"u": member.user_id})
    assert ended == [("unwelcomed",)]


def test_one_request_for_codes_at_a_time_a_person(db: Database, fake: WhatsAppFake) -> None:
    """Three an hour holds when requests for different numbers come at once: the second waits for the
    first's commit, then counts it."""
    world = _World(db, fake)
    member = world.person()
    assert world.prove(member).status_code == 200
    result: list[httpx.Response] = []
    with world.api.app_db.user(member.customer_id, member.user_id) as held:
        wa.lock_codes(held, member.user_id)
        waiting = threading.Thread(target=lambda: result.append(world.ask(member, new_number())))
        waiting.start()
        waiting.join(1.0)
        assert waiting.is_alive()  # held by the lock
        for _ in range(3):
            wa.add_code(held, member.customer_id, member.user_id, number_hash(NUMBER_KEY, new_number()),
                        "v1:x", b"x" * 32, "sent")  # fmt: skip
    waiting.join(10)
    assert _error(result[0])[0] == 429


# --- numbers with no connection, BLOCK, and WhatsApp's owner change -------------------------------------


def test_a_number_not_connected_is_told_once_a_day_and_never_after_block(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    number = new_number()
    world.from_phone(number, "hello")
    world.from_phone(number, "hello again")
    assert world.to(number) == [NOT_CONNECTED]
    hashed = number_hash(NUMBER_KEY, number)
    a_day = (
        "UPDATE whatsapp_unknown_numbers SET told_at = told_at - interval '25 hours' WHERE phone_hash = :h"
    )
    _owner(db, a_day, {"h": hashed})
    world.from_phone(number, "hello")
    assert world.to(number) == [NOT_CONNECTED, NOT_CONNECTED]
    world.from_phone(number, " Block ")
    _owner(db, a_day, {"h": hashed})
    world.from_phone(number, "hello")
    assert world.to(number) == [NOT_CONNECTED, NOT_CONNECTED]


def test_block_from_a_connected_number_ends_it(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    customer, _ = world.api.customer()
    person, _ = world.api.add_user(customer, "member")
    number = new_number()
    link = connect(db, customer, person, number)
    world.from_phone(number, "BLOCK")
    assert _ended(db, link) == ("person",)
    world.from_phone(number, "hello")
    assert world.to(number) == []  # blocked: never told


def _owner_change(world: _World, number: str, kind: str, sent_at: datetime) -> None:
    notice = {**message(number, sent_at), "type": "system", "system": {"body": "changed", "type": kind}}
    del notice["text"]
    assert post(world.api.client(), delivery(notice)).status_code == 200


@pytest.mark.parametrize(
    "kind", ["customer_changed_number", "user_changed_number", "customer_identity_changed"]
)
def test_whatsapps_owner_change_ends_the_connection_in_every_organisation(
    db: Database, fake: WhatsAppFake, kind: str
) -> None:
    world = _World(db, fake)
    customer, _ = world.api.customer()
    person, _ = world.api.add_user(customer, "member")
    number = new_number()
    link = connect(db, customer, person, number)
    sent_at = world.api.clock() - timedelta(hours=25)  # late, past the 24 hours: still heard
    _owner(
        db,
        "UPDATE whatsapp_links SET linked_at = :t WHERE id = :l",
        {"t": sent_at - timedelta(hours=1), "l": link},
    )
    _owner_change(world, number, kind, sent_at)
    assert _ended(db, link) == ("number_changed",)
    assert _owner(db, "SELECT count(*) FROM whatsapp_messages WHERE link_id = :l", {"l": link}) == [(0,)]
    world.from_phone(number, "hello")
    assert world.to(number) == [NOT_CONNECTED]


def test_a_connection_made_after_an_owner_change_outlives_its_late_notice(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    customer, _ = world.api.customer()
    person, _ = world.api.add_user(customer, "member")
    number = new_number()
    link = connect(db, customer, person, number)
    sent_at = world.api.clock() - timedelta(hours=2)
    _owner(
        db,
        "UPDATE whatsapp_links SET linked_at = :t WHERE id = :l",
        {"t": sent_at + timedelta(hours=1), "l": link},
    )
    _owner_change(world, number, "user_changed_number", sent_at)
    assert _ended(db, link) == (None,)


# --- Google, for one who signs in with Google only ------------------------------------------------------


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(oidc_router, "connect_limits", RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


def test_google_proves_it_is_them_only_with_their_own_fresh_sign_in(
    db: Database, fake: WhatsAppFake, google: FakeGoogle
) -> None:
    world = _World(db, fake)
    settings = google_settings(**{name: getattr(world.api.settings, name) for name in _WHATSAPP_SETTINGS})
    api = Api(db, settings=settings)
    customer, _ = api.customer()
    _on(db, customer)
    member, colleague, reviewer = (api.sign_in(customer, role) for role in ("member", "member", "reviewer"))
    now = int(api.clock().timestamp())

    def connect_google(signed: Signed) -> str:
        """Their own Google account, approved (its email is theirs): its subject."""
        subject = f"made-up-sub-{uuid.uuid4().hex}"
        started = signed.client.post(
            "/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF
        )
        query = google.sign_in(started.json()["url"], subject, signed.email, now)
        linked = signed.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
        assert "connected=google" in linked.headers["location"]
        return subject

    subject, theirs = connect_google(member), connect_google(colleague)
    connect_google(reviewer)
    with api.db.customer(customer) as conn:  # members sign in with Google only; reviewers not
        google_rules.save_rules(
            conn, customer, allowed_roles=["member", "reviewer", "admin"], only_roles=["member"], domains=[]
        )
    assert member.client.get("/api/whatsapp/me").json()["google"] is True
    not_only = reviewer.client.post("/api/whatsapp/prove/google", json={}, headers=CSRF)
    assert _error(not_only)[:2] == (403, "not_google_only")  # they confirm with their password

    def proof(sub: str, **claims: Any) -> str:
        started = member.client.post("/api/whatsapp/prove/google", json={}, headers=CSRF)
        assert started.status_code == 200, started.text
        assert "max_age=0" in started.json()["url"]
        query = google.sign_in(started.json()["url"], sub, member.email, now, **claims)
        back = member.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
        return urllib.parse.urlsplit(back.headers["location"]).geturl()

    fresh = {"auth_time": now - 10}
    assert proof(f"made-up-sub-{uuid.uuid4().hex}", **fresh) == "/settings/whatsapp?error=not_yours"
    assert proof(theirs, **fresh) == "/settings/whatsapp?error=not_yours"  # a colleague's, approved
    assert proof(subject) == "/settings/whatsapp?error=not_fresh"  # no auth_time
    assert proof(subject, auth_time=now - 301) == "/settings/whatsapp?error=not_fresh"
    asked = {"number": new_number(), "agreed": True}
    assert _error(member.client.post("/api/whatsapp/codes", json=asked, headers=CSRF))[:2] == (
        403,
        "proof_needed",
    )
    assert proof(subject, **fresh) == "/settings/whatsapp?proved=google"
    assert member.client.post("/api/whatsapp/codes", json=asked, headers=CSRF).json()["sent"] is True
    # The hourly limit is counted in the database (one audit row a start).
    starts = "SELECT count(*) FROM audit_log WHERE actor_user_id = :u AND action = 'whatsapp.google_proof'"
    assert _owner(db, starts, {"u": member.user_id}) == [(5,)]
    for _ in range(5):
        member.client.post("/api/whatsapp/prove/google", json={}, headers=CSRF)
    assert _error(member.client.post("/api/whatsapp/prove/google", json={}, headers=CSRF))[0] == 429


_WHATSAPP_SETTINGS = (
    "whatsapp_enabled",
    "whatsapp_graph_url",
    "whatsapp_phone_number_id",
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_verify_token",
    "whatsapp_number_key",
    "whatsapp_business_number",
)


# --- the Admin's --------------------------------------------------------------------------------------


def _save(admin: Signed, password: str | None = None, **changes: Any) -> httpx.Response:
    body = {
        "enabled": False,
        "roles": ["member", "reviewer"],
        "management": False,
        "voice": True,
        "uploads": False,
    }
    body |= changes
    if password is not None:
        body["password"] = password
    return admin.client.put("/api/admin/whatsapp", json=body, headers=CSRF)


def _settings_audit(db: Database, customer: str) -> list[tuple[Any, ...]]:
    return _owner(
        db,
        "SELECT action, details FROM audit_log WHERE customer_id = :c AND action LIKE 'settings.whatsapp%'"
        " ORDER BY id",
        {"c": customer},
    )


def test_the_admin_turns_it_on_with_the_password_and_management_and_admins_are_told(
    db: Database, fake: WhatsAppFake
) -> None:
    mailer = MemoryMailer()
    world = _World(db, fake, mailer)
    admin = world.api.new_admin()
    other_admin = world.api.new_admin_of(admin.customer_id)
    boss, boss_email = world.api.add_user(admin.customer_id, "member")
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": boss})
    world.api.add_user(admin.customer_id, "member")
    got = admin.client.get("/api/admin/whatsapp").json()
    assert got == {
        "enabled": False,
        "roles": ["member", "reviewer"],
        "management": False,
        "voice": True,
        "uploads": False,
        "available": True,
        "people": [],
    }
    no_tick = _save(admin, PASSWORD, enabled=True)
    assert _error(no_tick)[2] == "Confirm that your organisation's data rules allow WhatsApp."
    assert (
        _error(_save(admin, enabled=True, rules_confirmed=True))[2]
        == "Type your Kaagaz password to turn WhatsApp on."
    )
    assert _error(_save(admin, "not the password!!", enabled=True, rules_confirmed=True))[0] == 400
    assert world.api.user_state(admin.customer_id, admin.user_id)["failed_logins"] == 1
    on = _save(admin, PASSWORD, enabled=True, rules_confirmed=True)
    confirmed = (
        "SELECT rules_confirmed_by::text, rules_confirmed_at IS NOT NULL FROM whatsapp_org_settings"
        " WHERE customer_id = :c"
    )
    assert _owner(db, confirmed, {"c": admin.customer_id}) == [(admin.user_id, True)]
    assert on.status_code == 200 and on.json()["enabled"] is True
    told = _owner(db, "SELECT email FROM users WHERE customer_id = :c AND (role = 'admin' OR management)"
                  " AND id <> :u ORDER BY email", {"c": admin.customer_id, "u": admin.user_id})  # fmt: skip
    assert {other_admin.email, boss_email} <= {row[0] for row in told}
    assert sorted(sent.to for sent in mailer.sent) == [row[0] for row in told]
    assert {sent.subject for sent in mailer.sent} == {"WhatsApp was turned on for your organisation"}
    assert _save(admin, enabled=True).status_code == 200  # unchanged: no password, no audit
    assert _save(admin, PASSWORD, enabled=True, voice=False, roles=["reviewer", "member", "admin"]).json()[
        "roles"
    ] == [
        "member",
        "reviewer",
        "admin",
    ]
    assert _save(admin).json()["enabled"] is False
    common = {"management": False, "uploads": False}
    assert _settings_audit(db, admin.customer_id) == [
        (
            "settings.whatsapp_on",
            {**common, "enabled": True, "voice": True, "roles": "member,reviewer", "rules_confirmed": True},
        ),
        ("settings.whatsapp", {**common, "enabled": True, "voice": False, "roles": "member,reviewer,admin"}),
        ("settings.whatsapp_off", {**common, "enabled": False, "voice": True, "roles": "member,reviewer"}),
    ]
    assert len(mailer.sent) == len(told)


def test_it_turns_on_only_where_the_super_admin_leaves_it(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _owner(db, "INSERT INTO org_feature_switches (customer_id, feature, enabled)"
               " VALUES (:c, 'whatsapp', false)",
           {"c": admin.customer_id})  # fmt: skip
    assert admin.client.get("/api/admin/whatsapp").json()["available"] is False
    assert _error(_save(admin, PASSWORD, enabled=True, rules_confirmed=True))[:2] == (409, "not_available")


def test_switching_off_or_narrowing_the_roles_ends_those_connections_at_once(
    db: Database, fake: WhatsAppFake
) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _on(db, admin.customer_id)
    member, reviewer = world.person("member", admin.customer_id), world.person("reviewer", admin.customer_id)
    (_, member_link), (_, reviewer_link) = world.connected(member), world.connected(reviewer)
    assert _save(admin, enabled=True, roles=["member"]).status_code == 200
    assert _ended(db, reviewer_link) == ("not_allowed",) and _ended(db, member_link) == (None,)
    assert _save(admin).status_code == 200
    assert _ended(db, member_link) == ("not_allowed",)


def test_the_admin_sees_who_is_connected_and_can_disconnect_them(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _on(db, admin.customer_id)
    member = world.person("member", admin.customer_id)
    number, link = world.connected(member)
    people = admin.client.get("/api/admin/whatsapp").json()["people"]
    assert [(p["user_id"], p["last4"], p["last_used_at"]) for p in people] == [
        (member.user_id, number[-4:], None)
    ]
    stranger = world.person()
    assert (
        admin.client.post(f"/api/admin/whatsapp/{stranger.user_id}/disconnect", headers=CSRF).status_code
        == 404
    )
    gone = admin.client.post(f"/api/admin/whatsapp/{member.user_id}/disconnect", headers=CSRF)
    assert gone.status_code == 200 and gone.json()["people"] == []
    assert _ended(db, link) == ("admin",)
    assert _owner(
        db,
        "SELECT actor_user_id::text, subject_user_id::text FROM audit_log WHERE customer_id = :c"
        " AND action = 'whatsapp.disconnected'",
        {"c": admin.customer_id},
    ) == [(admin.user_id, member.user_id)]
    assert world.to(number)[-1] == ("kaagaz_disconnected", [])  # the bot tells them once
    assert (
        admin.client.post(f"/api/admin/whatsapp/{member.user_id}/disconnect", headers=CSRF).status_code == 404
    )
    assert (
        member.client.post(f"/api/admin/whatsapp/{member.user_id}/disconnect", headers=CSRF).status_code
        == 403
    )


def test_the_admins_routes_are_404_while_whatsapp_is_off(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    for method, path in (("GET", "/api/admin/whatsapp"), ("PUT", "/api/admin/whatsapp"),
                         ("POST", f"/api/admin/whatsapp/{admin.user_id}/disconnect")):  # fmt: skip
        assert call(admin.client, method, path).status_code == 404
    assert "whatsapp" not in admin.client.get("/api/auth/me").json()


def test_a_codes_number_is_never_logged(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    fake.failures = [500]
    with caplog.at_level("DEBUG"):
        world.proved_ask(member, number)
        world.from_phone(new_number(), "hello")
    assert number[1:] not in caplog.text and number[-7:] not in caplog.text


def test_ticking_admins_or_management_later_asks_for_the_password(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _on(db, admin.customer_id)
    for tick in ({"roles": ["member", "reviewer", "admin"]}, {"management": True}):
        refused = _save(admin, enabled=True, **tick)
        assert _error(refused)[2] == "Type your Kaagaz password to let Admins or Management use WhatsApp."
        assert _error(_save(admin, "not the password!!", enabled=True, **tick))[0] == 400
        assert _save(admin, PASSWORD, enabled=True, **tick).status_code == 200
        assert _save(admin, enabled=True).status_code == 200  # taking it away needs none


def test_the_admins_disconnect_holds_when_the_number_no_longer_opens(
    db: Database, fake: WhatsAppFake, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World(db, fake)
    admin = world.api.new_admin()
    _on(db, admin.customer_id)
    member = world.person("member", admin.customer_id)
    number, link = world.connected(member)
    _owner(db, "UPDATE whatsapp_links SET phone_sealed = 'v1:made-up' WHERE id = :l", {"l": link})
    with caplog.at_level("WARNING"):
        gone = admin.client.post(f"/api/admin/whatsapp/{member.user_id}/disconnect", headers=CSRF)
    assert gone.status_code == 200 and _ended(db, link) == ("admin",)
    assert ("kaagaz_disconnected", []) not in world.to(number)  # untold: no number to tell
    assert [getattr(record, "code", None) for record in caplog.records] == ["number_sealed"]


def test_signing_out_ends_the_proof_and_the_code_steps_need_it(db: Database, fake: WhatsAppFake) -> None:
    world = _World(db, fake)
    member = world.person()
    number = new_number()
    world.proved_ask(member, number)
    world.api.clock.advance(10 * 60 + 1)  # the proof is over; the code is still on
    assert _error(world.verify(member, world.fake.code_to(number)))[:2] == (403, "proof_needed")
    assert _error(member.client.post("/api/whatsapp/codes/phone/confirm", headers=CSRF))[:2] == (
        403,
        "proof_needed",
    )
    assert world.prove(member).status_code == 200
    out = member.client.post("/api/auth/logout", headers=CSRF)
    assert any(
        cookie.startswith("kaagaz_whatsapp_proof=")
        and "Path=/api/whatsapp" in cookie
        and "Max-Age=0" in cookie
        for cookie in out.headers.get_list("set-cookie")
    )


def test_the_sweep_empties_an_expired_codes_number(db: Database, fake: WhatsAppFake) -> None:
    """A stranger's number is never kept past its code's 10 minutes; the row stays for the limits."""
    world = _World(db, fake)
    member = world.person()
    world.proved_ask(member, new_number())
    _owner(db, "UPDATE whatsapp_link_codes SET created_at = created_at - interval '11 minutes',"
               " expires_at = expires_at - interval '11 minutes' WHERE user_id = :u",
           {"u": member.user_id})  # fmt: skip
    with world.api.app_db.system() as conn:
        wa.sweep(conn)
    left = "SELECT phone_sealed IS NULL FROM whatsapp_link_codes WHERE user_id = :u"
    assert _owner(db, left, {"u": member.user_id}) == [(True,)]
