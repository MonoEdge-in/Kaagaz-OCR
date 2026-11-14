"""What Meta's webhook delivers (#603): the signature over the raw bytes, the messages to our own
number only, numbers found by their keyed hash and sealed to their person; the settings that turn
WhatsApp on; and the reply sent to the fake WhatsApp."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.channel import inbound
from kaagaz.channel.numbers import e164, number_hash, open_number, seal
from kaagaz.settings import Settings, SettingsError
from tests.test_settings import shipped
from tests.whatsapp_fake import (
    APP_SECRET,
    NUMBER_ID,
    NUMBER_KEY,
    TOKEN,
    delivery,
    message,
    running_fake,
    signature,
)

SENT = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
NUMBER = "+15550001122"


def test_the_signature_is_over_the_raw_bytes_with_the_app_secret() -> None:
    body = json.dumps(delivery(message(NUMBER, SENT, "नमस्कार")), ensure_ascii=True).encode()
    assert inbound.signed(APP_SECRET, body, signature(body))
    rewritten = json.dumps(json.loads(body), ensure_ascii=False).encode()  # the same JSON, other bytes
    assert not inbound.signed(APP_SECRET, rewritten, signature(body))
    assert not inbound.signed(APP_SECRET, body, signature(body, "another-secret"))
    assert not inbound.signed(APP_SECRET, body + b" ", signature(body))
    for header in (
        None,
        "",
        signature(body).removeprefix("sha256="),
        "sha1=" + signature(body)[7:],
        "sha256=é",
    ):
        assert not inbound.signed(APP_SECRET, body, header), header


def test_only_well_formed_messages_to_our_own_number_are_read() -> None:
    tapped = {
        **message(NUMBER, SENT),
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": "g7r3", "title": "Accounts"}},
    }
    del tapped["text"]
    voice = {"from": NUMBER[1:], "id": "wamid.voice", "timestamp": "1763114400", "type": "audio",
             "audio": {"id": "media-1", "voice": True}, "context": {"forwarded": True}}  # fmt: skip
    odd: list[Any] = [
        {**message(NUMBER, SENT), "id": ""},
        {**message(NUMBER, SENT), "timestamp": "soon"},
        {**message(NUMBER, SENT), "from": "12"},
        "not a message",
    ]
    payload = delivery(message(NUMBER, SENT, "Bill total?", wamid="wamid.1"), tapped, voice, *odd)
    payload["entry"].append(delivery(message(NUMBER, SENT), number_id="999")["entry"][0])  # another number
    payload["entry"].append({"changes": [{"field": "statuses", "value": {"statuses": [{"id": "x"}]}}]})
    found = inbound.messages(payload, NUMBER_ID)
    assert [(m.wamid, m.number, m.kind, m.body, m.media_id, m.forwarded) for m in found] == [
        ("wamid.1", NUMBER, "text", "Bill total?", None, False),
        (tapped["id"], NUMBER, "interactive", "g7r3", None, False),
        ("wamid.voice", NUMBER, "audio", None, "media-1", True),
    ]
    assert found[0].sent_at == SENT
    sticker = {**message(NUMBER, SENT), "type": "sticker", "sticker": {"id": "s"}}
    assert [(m.kind, m.body) for m in inbound.messages(delivery(sticker), NUMBER_ID)] == [("other", None)]
    assert inbound.messages({"entry": "x"}, NUMBER_ID) == inbound.messages([], NUMBER_ID) == []


def test_a_number_is_found_by_its_keyed_hash_and_opens_only_for_its_own_person() -> None:
    assert e164("15550001122") == e164(" +15550001122 ") == NUMBER
    assert [e164(bad) for bad in ("+0991", "12345", "+1 555 000 1122", "")] == [None] * 4
    hashed = number_hash(NUMBER_KEY, NUMBER)
    assert len(hashed) == 32 and hashed == number_hash(NUMBER_KEY, NUMBER)
    assert hashed not in (
        number_hash(NUMBER_KEY, "+15550001123"),
        number_hash("other-key-" + "x" * 32, NUMBER),
    )
    sealed = seal(NUMBER_KEY, NUMBER, "c1", "u1")
    assert NUMBER[1:] not in sealed and open_number(NUMBER_KEY, sealed, "c1", "u1") == NUMBER
    for customer, user, key in (("c1", "u2", NUMBER_KEY), ("c2", "u1", NUMBER_KEY), ("c1", "u1", "x" * 40)):
        with pytest.raises(SecretBoxError):
            open_number(key, sealed, customer, user)


ON = {
    "WHATSAPP_ENABLED": "true",
    "WHATSAPP_GRAPH_URL": "https://graph.example.test/v0.0/",
    "WHATSAPP_PHONE_NUMBER_ID": NUMBER_ID,
    "WHATSAPP_ACCESS_TOKEN": TOKEN,
    "WHATSAPP_APP_SECRET": APP_SECRET,
    "WHATSAPP_VERIFY_TOKEN": "verify-me",
    "WHATSAPP_NUMBER_KEY": NUMBER_KEY,
}


def test_whatsapp_is_off_unless_set_and_on_needs_every_setting() -> None:
    off = Settings.from_env(shipped())
    assert (off.whatsapp_enabled, off.whatsapp_app_secret, off.whatsapp_people_at_once) == (False, None, 4)
    assert Settings.from_env(shipped(**{**ON, "WHATSAPP_ENABLED": "false"})).whatsapp_number_key is None
    on = Settings.from_env(shipped(**ON))
    assert (on.whatsapp_enabled, on.whatsapp_graph_url, on.whatsapp_number_key) == (
        True,
        "https://graph.example.test/v0.0",
        NUMBER_KEY,
    )
    assert APP_SECRET not in repr(on) and NUMBER_KEY not in repr(on) and TOKEN not in repr(on)
    for name in ON:
        if name != "WHATSAPP_ENABLED":
            with pytest.raises(SettingsError, match=name):
                Settings.from_env(shipped(**{**ON, name: None}))
    for name, bad in (
        ("WHATSAPP_GRAPH_URL", "http://graph.example.test/v0.0"),
        ("WHATSAPP_PHONE_NUMBER_ID", "12ab"),
        ("WHATSAPP_APP_SECRET", "has space"),
        ("WHATSAPP_NUMBER_KEY", "short"),
        ("WHATSAPP_PEOPLE_AT_ONCE", "11"),
    ):
        with pytest.raises(SettingsError, match=name):
            Settings.from_env(shipped(**{**ON, name: bad}))


def test_a_reply_is_one_text_with_link_previews_off_and_the_token_in_its_header_only() -> None:
    with running_fake() as fake:
        sender = MetaWhatsApp(fake.url, NUMBER_ID, TOKEN)
        sender.send_text(NUMBER, "Open: a link")
        ((path, headers, body),) = fake.requests
        assert path == f"/v0.0/{NUMBER_ID}/messages"
        assert headers["authorization"] == f"Bearer {TOKEN}" and TOKEN not in json.dumps(body)
        assert body == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": NUMBER[1:],
            "type": "text",
            "text": {"preview_url": False, "body": "Open: a link"},
        }
        fake.failures = [400, 0]
        with pytest.raises(WhatsAppError) as refused:
            sender.send_text(NUMBER, "x")
        assert (refused.value.code, refused.value.status) == ("refused", 400)
        with pytest.raises(WhatsAppError) as lost:
            sender.send_text(NUMBER, "x")
        assert (lost.value.code, str(lost.value)) == ("unavailable", "unavailable")
        assert NUMBER not in str(refused.value) and TOKEN not in str(refused.value)
