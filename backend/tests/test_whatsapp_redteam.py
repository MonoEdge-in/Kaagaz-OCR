"""W7, the red team (#603, PRD 7 section 15 "Tests and done when"), without a database: ways round
ID-number masking. A number written with a dash other than the hyphen and en dash, or with
characters between its digits that draw nothing, reads whole on a phone, so it is masked as one."""

import pytest

from kaagaz.channel.texts import MASK, mask
from kaagaz.channel.voice import opus_packets_seconds
from tests.whatsapp_fake import ogg_opus

ZWJ = "\u200d"  # the zero-width joiner


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        # Unicode's other dashes: the non-breaking hyphen (models write it), hyphen, figure dash,
        # em dash, minus, full-width hyphen-minus.
        ("Aadhaar 1234\u20115678\u20119012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u20105678\u20109012", f"Aadhaar {MASK}9012"),
        ("Card 4111\u20121111\u20121111\u20121111", f"Card {MASK}1111"),
        ("Card 4111\u20141111\u20141111\u20141111", f"Card {MASK}1111"),
        ("Card 4111\u22121111\u22121111\u22121111", f"Card {MASK}1111"),
        ("Aadhaar 1234\uff0d5678\uff0d9012", f"Aadhaar {MASK}9012"),
        # Characters that draw nothing: zero-width space, soft hyphen, word joiner, a mark of direction.
        ("Aadhaar 1234\u200b5678\u200b9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u00ad5678\u00ad9012", f"Aadhaar {MASK}9012"),
        ("Account 0012\u20603456\u20607890", f"Account {MASK}7890"),
        ("Account 001234\u200e567890", f"Account {MASK}7890"),
        ("Aadhaar 1234" + ZWJ * 4 + "5678 9012", f"Aadhaar {MASK}9012"),
        (
            "Aadhaar \u0967\u0968\u0969\u096a"
            + ZWJ
            + "\u096b\u096c\u096d\u096e"
            + ZWJ
            + "\u096f\u0966\u0967\u0968",
            f"Aadhaar {MASK}\u096f\u0966\u0967\u0968",
        ),
        ("PAN ABCDE\u200b1234F", f"PAN {MASK}234F"),
    ],
)
def test_a_number_that_reads_whole_is_masked_whatever_sits_between_its_digits(text: str, masked: str) -> None:
    assert mask(text) == (masked, True)


def test_the_joiners_that_shape_a_devanagari_word_are_kept() -> None:
    eyelash = "\u0930\u094d" + ZWJ + "\u092f\u093e"  # Marathi's eyelash ra is drawn with the joiner
    assert mask(f"{eyelash} 2026") == (f"{eyelash} 2026", False)
    assert mask("invoice 2026\u200b") == ("invoice 2026", False)  # gone, but nothing was masked


def test_a_voice_notes_audio_plays_as_long_as_its_packets_whatever_its_pages_claim() -> None:
    """W6's length is the last page's granule position, which the sender writes: the packets' own
    frame sizes say how long the audio plays (each of the fake's packets: 20 ms)."""
    assert opus_packets_seconds(ogg_opus(5, pad=200 * 100)) == pytest.approx(2.0)
    assert opus_packets_seconds(ogg_opus(5, pad=200 * 3100)) == pytest.approx(62.0)
    assert opus_packets_seconds(b"not ogg") == 0
