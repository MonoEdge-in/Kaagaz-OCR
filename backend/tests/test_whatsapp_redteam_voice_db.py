"""W7, the red team on W6's voice notes (#603, PRD 7 section 15 "Voice notes"), end to end on the
fake WhatsApp with a fake model writing notes down.

* A note's length is its sender's to claim: the last Ogg page's granule position. A note claiming
  5 seconds but carrying more than a minute of audio is refused before the model hears any of it
  (the audio packets themselves say how long it plays).
* What the model heard is untrusted: an ID number split by a dash a phone draws as one, or a link,
  never reaches "I heard: ..."; nor do the words heard end the connection.
"""

from collections.abc import Iterator

import pytest

from kaagaz.channel.texts import say
from kaagaz.db.engine import Database
from tests.test_operator_auth_db import _owner
from tests.test_whatsapp_voice_db import Ears, World
from tests.whatsapp_fake import WhatsAppFake, ogg_opus, running_fake

pytestmark = pytest.mark.db

EVIL = "evil.example"
FRAME = 200  # the fake recording's bytes of audio a packet: 20 ms each


@pytest.fixture
def fake() -> Iterator[WhatsAppFake]:
    with running_fake() as running:
        yield running


def test_a_note_claiming_5_seconds_but_playing_over_a_minute_is_refused_unheard(
    db: Database, fake: WhatsAppFake
) -> None:
    world = World(db, fake)
    person = world.person()
    long = ogg_opus(5, pad=FRAME * 3100)  # 3,100 packets of 20 ms: 62 seconds of audio
    assert len(long) < 1024 * 1024  # under the size limit: only its length can refuse it
    assert world.speak(person, long) == say("voice_limit", "en")
    assert world.ears.audio == [] and world.api.answers.asked == []
    world.speak(person, ogg_opus(5, pad=FRAME * 2900))  # 58 seconds: heard
    assert len(world.ears.audio) == 1


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
