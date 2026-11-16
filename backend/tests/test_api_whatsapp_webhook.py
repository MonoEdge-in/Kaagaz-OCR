"""Meta's WhatsApp webhook (#603, PRD 7 section 15 W2): 404 while WhatsApp is off; Meta's setup
check; a signed message from a connected number stored once, in its person's transaction; and
dropped, storing nothing: an unsigned or wrongly signed delivery, a message older than WhatsApp's
24 hours (a replay inside them is stopped by the message id), a message for another number, a
number not connected. A signed body nothing can read never answers 500; a body over 3 MB is refused
unread, a lost database is 503 (Meta sends it again), and the webhook has its own rate bucket."""

import json
import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from kaagaz.api.routers import whatsapp as router
from kaagaz.db import whatsapp as wa
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, WHATSAPP_ROUTES, Api, call
from tests.whatsapp_fake import (
    VERIFY_TOKEN,
    WEBHOOK,
    connect,
    delivery,
    message,
    new_number,
    post,
    signature,
    whatsapp_settings,
)

pytestmark = pytest.mark.db


def _stored(db: Database, customer_id: str) -> list[tuple[Any, ...]]:
    """The customer's messages, as the owner (no row level security): what the webhook stored."""
    with db.engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT wamid, link_id::text, user_id::text, kind, body, state, sent_at"
                " FROM whatsapp_messages WHERE customer_id = :c ORDER BY received_at, wamid"
            ),
            {"c": customer_id},
        )
        return [tuple(row) for row in rows]


def _connected(api: Api) -> tuple[str, str, str, str]:
    """A member with a connected number: (customer, member, number, link)."""
    customer, _ = api.customer()
    member, _ = api.add_user(customer, "member")
    number = new_number()
    return customer, member, number, connect(api.db, customer, member, number)


@pytest.mark.parametrize(("method", "path"), WHATSAPP_ROUTES)
def test_every_route_is_404_while_whatsapp_is_off(db: Database, method: str, path: str) -> None:
    api = Api(db)
    client = api.client()
    assert call(client, method, path).status_code == 404
    if path != WEBHOOK:  # Changed on purpose (W3): Account's routes take the CSRF header first, as all
        return
    body = json.dumps(delivery()).encode()
    reply = client.request(method, path, content=body, headers={"X-Hub-Signature-256": signature(body)})
    assert reply.status_code == 404


def test_metas_setup_check_gets_its_challenge_back_only_with_our_token(db: Database) -> None:
    client = Api(db, settings=whatsapp_settings()).client()
    asked = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"}
    reply = client.get(WEBHOOK, params=asked)
    assert (reply.status_code, reply.text, reply.headers["content-type"].split(";")[0]) == (
        200,
        "1158201444",
        "text/plain",
    )
    for wrong in ({"hub.verify_token": "not-ours"}, {"hub.mode": "unsubscribe"}, {"hub.verify_token": ""}):
        assert client.get(WEBHOOK, params={**asked, **wrong}).status_code == 403


def test_a_signed_message_from_a_connected_number_is_stored_once_in_its_persons_name(db: Database) -> None:
    api = Api(db, settings=whatsapp_settings())
    customer, member, number, link = _connected(api)
    client = api.client()  # no cookie, and no CSRF header: Meta sends neither
    first = message(number, api.clock(), "Total of the March bill?", wamid="wamid.once")
    assert post(client, delivery(first)).json() == {"ok": True}
    assert post(client, delivery(first, first)).status_code == 200  # delivered again, twice in one
    assert _stored(db, customer) == [
        (
            "wamid.once",
            link,
            member,
            "text",
            "Total of the March bill?",
            "waiting",
            api.clock().replace(microsecond=0),
        )
    ]


def test_an_unsigned_or_wrongly_signed_delivery_is_refused_and_stores_nothing(db: Database) -> None:
    api = Api(db, settings=whatsapp_settings())
    customer, _, number, _ = _connected(api)
    client = api.client()
    body = json.dumps(delivery(message(number, api.clock()))).encode()
    for sign in ("sha256=" + "0" * 64, signature(body, "another-app-secret"), signature(body + b" ")):
        reply = post(client, body, sign=sign)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    unsigned = client.post(WEBHOOK, content=body, headers={**CSRF, "Content-Type": "application/json"})
    assert unsigned.status_code == 403
    assert _stored(db, customer) == []


def test_a_message_past_24_hours_and_one_for_another_number_are_dropped_and_a_replay_kept_once(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """The lead's decision on #603's review: WhatsApp's own 24 hours (it can't be answered after),
    not 10 minutes; a replay within them is the same message id, kept once."""
    api = Api(db, settings=whatsapp_settings())
    customer, _, number, _ = _connected(api)
    client = api.client()
    old = message(number, api.clock() - timedelta(hours=24, seconds=1), wamid="wamid.old")
    recent = message(number, api.clock() - timedelta(hours=23), wamid="wamid.recent")
    elsewhere = message(number, api.clock(), wamid="wamid.elsewhere")
    with caplog.at_level(logging.WARNING, logger="kaagaz.api.whatsapp"):
        assert post(client, delivery(old, recent)).status_code == 200
    assert [(r.levelname, getattr(r, "code", None)) for r in caplog.records] == [("WARNING", "too_old")]
    assert post(client, delivery(elsewhere, number_id="987654321")).status_code == 200
    assert post(client, delivery(recent)).status_code == 200  # replayed an hour later: kept once
    assert [row[0] for row in _stored(db, customer)] == ["wamid.recent"]


def test_a_signed_body_nothing_can_read_never_answers_500(db: Database) -> None:
    """#603 review: Meta would send a 500's body again for days. Each is answered 200; what can be
    kept is kept, with NUL and a lone surrogate gone from its text (JSON escapes in the raw body)."""
    api = Api(db, settings=whatsapp_settings())
    customer, _, number, _ = _connected(api)
    client = api.client()
    deep = b"[" * 100_000 + b"]" * 100_000  # past the JSON reader's depth
    assert post(client, deep).json() == {"ok": True}
    odd = [
        message(number, api.clock(), wamid="wamid.NUL"),  # a NUL in the id: dropped
        message(number, api.clock(), wamid="wamid." + "x" * 200),  # too long: dropped
        {**message(number, api.clock()), "timestamp": "9" * 12},  # past 11 digits: dropped
        message(number, api.clock(), "aNULbSURROGATEc", wamid="wamid.kept"),  # kept, cleaned
    ]
    escaped = json.dumps(delivery(*odd)).replace("NUL", "\\u0000").replace("SURROGATE", "\\ud800")
    assert post(client, escaped.encode()).json() == {"ok": True}
    assert [(row[0], row[4]) for row in _stored(db, customer)] == [("wamid.kept", "abc")]


def test_a_number_not_connected_or_no_longer_connected_stores_nothing(db: Database) -> None:
    api = Api(db, settings=whatsapp_settings())
    customer, member, number, link = _connected(api)
    client = api.client()
    stranger = message(new_number(), api.clock(), wamid="wamid.stranger")
    assert post(client, delivery(stranger)).status_code == 200
    with db.user(customer, member) as conn:
        assert conn.execute(text("SELECT kaagaz_whatsapp_end_link(:l, 'person')"), {"l": link}).scalar_one()
    assert post(client, delivery(message(number, api.clock()))).status_code == 200
    assert _stored(db, customer) == []
    with db.engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM whatsapp_messages WHERE wamid = 'wamid.stranger'")
            ).scalar()
            == 0
        )


def test_a_body_over_three_megabytes_is_refused_before_it_is_read(db: Database) -> None:
    client = Api(db, settings=whatsapp_settings()).client()
    body = b"{" + b" " * router.MAX_BODY_BYTES + b"}"
    reply = post(client, body)
    assert (reply.status_code, reply.json()["error"]["code"]) == (413, "too_large")


def test_a_body_that_is_not_json_is_answered_and_dropped(db: Database) -> None:
    assert post(Api(db, settings=whatsapp_settings()).client(), b"not json").json() == {"ok": True}


def test_a_lost_database_is_503_so_meta_sends_the_message_again(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db, settings=whatsapp_settings())
    _, _, number, _ = _connected(api)

    def lost(*_: object) -> None:
        raise OperationalError("SELECT", {}, Exception("the server closed the connection"))

    monkeypatch.setattr(wa, "lookup", lost)
    reply = post(api.client(), delivery(message(number, api.clock())))
    assert (reply.status_code, reply.json()["error"]["code"]) == (503, "backend_unavailable")


def test_the_webhook_has_its_own_rate_bucket(db: Database) -> None:
    """Meta's deliveries come from a few addresses: they never use up, or are refused by, the
    general bucket of that address."""
    api = Api(db, settings=whatsapp_settings(rate_limit_per_minute=2, whatsapp_webhook_calls_per_minute=4))
    client = api.client()
    payload = delivery()
    assert [post(client, payload).status_code for _ in range(5)] == [200, 200, 200, 200, 429]
    assert [client.get("/api/health").status_code for _ in range(3)][:2] != [429, 429]
