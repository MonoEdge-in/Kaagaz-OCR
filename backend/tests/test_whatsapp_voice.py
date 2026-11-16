"""Kaagaz on WhatsApp's voice notes (#603 W6), with no database: a voice note's length read from its
Ogg pages, and ``OpenRouterClient.transcribe``'s request on the fake OpenRouter: the provider rules
every model call carries (Google Vertex only, zero data retention), the audio as an ``input_audio``
part, no repair turn (it would send the audio again), billed to the scope it runs in, nothing of it
logged."""

import base64
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.ai import usage
from kaagaz.ai.client import ModelOutputError
from kaagaz.ai.openrouter import OpenRouterClient
from kaagaz.channel.voice import _SCHEMA, Transcriber, ogg_opus_seconds
from tests.openrouter_fake import OpenRouterFake, chat_reply, json_reply, running_fake
from tests.openrouter_fake import usage as counts
from tests.test_model_choice_contracts import CUSTOMER, USER, _Conn, _db
from tests.test_openrouter_client import THE_PROVIDER_BLOCK, _client
from tests.whatsapp_fake import PRE_SKIP, ogg_opus

HEARD: dict[str, Any] = {"text": "बिजली का बिल कितना है", "language": "hi", "sure": True}


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


# --- how long a voice note is ------------------------------------------------------------------


@pytest.mark.parametrize("seconds", [0.5, 5, 59.9, 60, 60.9, 61, 600])
def test_a_voice_notes_length_is_its_last_pages_granule_less_the_pre_skip(seconds: float) -> None:
    assert ogg_opus_seconds(ogg_opus(seconds)) == pytest.approx(seconds, abs=1 / 48000)
    assert ogg_opus_seconds(ogg_opus(seconds, pad=5000)) == pytest.approx(seconds, abs=1 / 48000)


def test_a_last_page_with_no_end_counts_the_page_before() -> None:
    whole = ogg_opus(7, pad=600)
    last = whole.rfind(b"OggS")
    unfinished = (
        whole[:last] + whole[last : last + 6] + (-1).to_bytes(8, "little", signed=True) + whole[last + 14 :]
    )
    assert ogg_opus_seconds(unfinished) == pytest.approx(7 * 2 / 3, abs=1 / 48000)  # 3 pages, 2 end
    assert ogg_opus_seconds(whole + unfinished[last:]) == pytest.approx(7, abs=1 / 48000)
    only_headers = ogg_opus(7)[: ogg_opus(7).rfind(b"OggS")]
    assert ogg_opus_seconds(only_headers) is None  # the header pages' granule positions are 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"OggS",
        b"ID3\x04" + b"\0" * 400,  # an MP3
        b"\0\0\0\x18ftypM4A " + b"\0" * 400,  # an M4A
        ogg_opus(5).replace(b"OpusHead", b"OpusHeaX", 1),  # Ogg, but not Opus (Vorbis, say)
        ogg_opus(5)[:40],  # cut off inside its OpusHead
        b"OggS\x01" + ogg_opus(5)[5:],  # an Ogg version that does not exist
    ],
)
def test_anything_but_ogg_opus_has_no_length(data: bytes) -> None:
    assert ogg_opus_seconds(data) is None


def test_the_pre_skip_is_left_out() -> None:
    data = ogg_opus(10)
    assert int.from_bytes(data[27 + 1 + 10 : 27 + 1 + 12], "little") == PRE_SKIP
    assert ogg_opus_seconds(data) == pytest.approx(10, abs=1 / 48000)


# --- the request -------------------------------------------------------------------------------


def _transcribe(client: OpenRouterClient, audio: bytes) -> dict[str, object]:
    return client.transcribe(
        system="Write it down.", audio=audio, audio_format="ogg", schema=_SCHEMA, max_tokens=1000
    )


def test_the_note_goes_as_input_audio_under_vertex_only_and_zero_retention(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(HEARD, counts=counts(800, 40, 0)))
    client = _client(fake.url)
    audio = ogg_opus(3)
    assert isinstance(client, Transcriber)
    assert _transcribe(client, audio) == HEARD
    (request,) = fake.requests
    assert request.body["provider"] == THE_PROVIDER_BLOCK
    assert request.body["model"] == client.chat_model
    assert request.body["messages"] == [
        {"role": "system", "content": "Write it down."},
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": base64.b64encode(audio).decode(), "format": "ogg"},
                }
            ],
        },
    ]
    assert request.body["response_format"]["json_schema"]["schema"] == _SCHEMA
    assert request.body["stream"] is False


def test_a_reply_of_another_shape_is_never_sent_back_with_the_audio(fake: OpenRouterFake) -> None:
    fake.script("completions", chat_reply("not json"), json_reply(HEARD))
    with pytest.raises(ModelOutputError) as raised:
        _transcribe(_client(fake.url), ogg_opus(3))
    assert raised.value.code == "invalid_output"
    assert len(fake.requests) == 1  # no repair turn: the audio went once


def test_the_call_is_billed_to_the_scope_it_runs_in_as_transcribe(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(HEARD, counts=counts(800, 40, 0, cost=0.0007)))
    ledger, db = _db(_Conn())
    with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="transcribe"):
        _transcribe(_client(fake.url), ogg_opus(3))
    [rows] = ledger.conn.sent
    assert [(row["kind"], row["user_id"], row["calls"], row["input_tokens"]) for row in rows] == [
        ("transcribe", USER, 1, 800)
    ]


def test_nothing_heard_or_sent_is_logged(fake: OpenRouterFake, caplog: pytest.LogCaptureFixture) -> None:
    fake.script("completions", json_reply(HEARD), chat_reply("not json"))
    caplog.set_level(logging.DEBUG)
    client = _client(fake.url)
    audio = ogg_opus(3)
    _transcribe(client, audio)
    with pytest.raises(ModelOutputError):
        _transcribe(client, audio)
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert HEARD["text"] not in logged and base64.b64encode(audio).decode()[:40] not in logged
