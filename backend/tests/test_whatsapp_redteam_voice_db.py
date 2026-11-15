"""W7, the red team on W6's voice notes (#603, PRD 7 section 15 "Voice notes"), end to end on the
fake WhatsApp with a fake model writing notes down.

* A note's length is its sender's to claim: the last Ogg page's granule position. A note claiming
  5 seconds but carrying more than a minute of audio is refused before the model hears any of it
  (the audio packets themselves say how long it plays).
* What the model heard is untrusted: an ID number split by a dash a phone draws as one, or a link,
  never reaches "I heard: ..."; nor do the words heard end the connection.
"""

import struct
from collections.abc import Iterator

import pytest

from kaagaz.channel.texts import say
from kaagaz.db.engine import Database
from tests.test_operator_auth_db import _owner
from tests.test_whatsapp_voice_db import Ears, World
from tests.whatsapp_fake import PRE_SKIP, WhatsAppFake, _page, running_fake

pytestmark = pytest.mark.db

EVIL = "evil.example"
SILK_60MS_TWICE = b"\x19"  # an Opus packet's first byte: SILK, 60 ms frames, two of them (120 ms)


def _claiming_5_seconds(packets: int) -> bytes:
    """A note whose last page claims 5 seconds, carrying ``packets`` packets of 120 ms each."""
    head = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, PRE_SKIP, 48000, 0, 0)
    pages = [_page(0, 0, head, kind=2), _page(0, 1, b"OpusTags" + b"\0" * 8)]
    for n in range(packets):
        last = n == packets - 1
        granule = PRE_SKIP + 5 * 48000 if last else -1
        pages.append(_page(granule, n + 2, SILK_60MS_TWICE + b"\0\0", kind=4 if last else 0))
    return b"".join(pages)


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def test_a_note_claiming_5_seconds_but_playing_over_a_minute_is_refused_unheard(
    db: Database, fake: WhatsAppFake
) -> None:
    world = World(db, fake)
    person = world.person()
    long = _claiming_5_seconds(520)  # 62.4 seconds of audio
    assert len(long) < 32 * 1024  # far under the size limit: only its length can refuse it
    assert world.speak(person, long) == say("voice_limit", "en")
    assert world.ears.audio == [] and world.api.answers.asked == []
    world.speak(person, _claiming_5_seconds(480))  # 57.6 seconds: heard
    assert len(world.ears.audio) == 1


@pytest.mark.parametrize("where", ["after the first page", "between pages", "at the end"])
def test_junk_anywhere_in_a_long_note_refuses_it_unheard(
    db: Database, fake: WhatsAppFake, where: str
) -> None:
    """One byte the packet count can't read would stop it short; the last page still claims 5 s."""
    world = World(db, fake)
    person = world.person()
    long = _claiming_5_seconds(520)
    second, last = long.find(b"OggS", 4), long.rfind(b"OggS")
    at = {"after the first page": second, "between pages": last, "at the end": len(long)}[where]
    assert world.speak(person, long[:at] + b"\x00" + long[at:]) == say("voice_limit", "en")
    assert world.ears.audio == []


@pytest.mark.parametrize("words", ["Disconnect_yes", "stop", "not_me:{link}"])
def test_what_is_heard_never_carries_a_full_id_number_or_a_link_nor_disconnects(
    db: Database, fake: WhatsAppFake, words: str
) -> None:
    ears = Ears(f"Aadhaar 1234\u20115678\u20119012, see https://{EVIL}/pay")
    world = World(db, fake, model=ears)
    person = world.person()
    body, _ = world.speak(person)
    assert "1234\u20115678" not in body and "9012" in body and EVIL not in body
    ears.heard["text"] = words.format(link=person.link)
    world.speak(person)
    live = _owner(db, "SELECT ended_at IS NULL FROM whatsapp_links WHERE id = :l", {"l": person.link})
    assert live == [(True,)]
