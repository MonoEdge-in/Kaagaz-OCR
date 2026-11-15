"""W7, the red team (#603, PRD 7 section 15 "Tests and done when"), without a database.

* ID-number masking: a number written with a dash other than the hyphen and en dash, or with
  characters between its digits that draw nothing, reads whole on a phone, so it is masked as one.
* Links: none but Kaagaz's own goes; a host with no scheme is made safe, its text kept.
* A voice note's length by its own audio packets; junk anywhere makes it unknowable.
"""

import math

import pytest

from kaagaz.channel.texts import MASK, kaagaz_links_only, mask
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
    assert opus_packets_seconds(b"not ogg") == math.inf


def test_junk_anywhere_in_a_voice_note_makes_its_length_unknowable() -> None:
    """A byte the walk can't read would stop the count short while the last page still claims 5 s."""
    note = ogg_opus(5, pad=200 * 10)
    second, last = note.find(b"OggS", 4), note.rfind(b"OggS")
    for broken in (
        note[:second] + b"\x00" + note[second:],
        note[:last] + b"\x00" + note[last:],
        note + b"\x00",
    ):
        assert opus_packets_seconds(broken) == math.inf


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        # More that draws nothing: a variation selector, the combining grapheme joiner, the Hangul and
        # half-width fillers, the braille blank, a combining mark after a digit.
        ("Aadhaar 1234\ufe0f5678\ufe0f9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u034f5678\u034f9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u31645678\u31649012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\uffa05678\uffa09012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u115f5678\u11609012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u28005678\u28009012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234\u03015678\u03019012", f"Aadhaar {MASK}9012"),
        # A PAN with a space or hyphen between its parts, in full-width letters, or other digits.
        ("PAN ABCDE 1234 F", f"PAN {MASK}234F"),
        ("PAN ABCDE-1234-F", f"PAN {MASK}234F"),
        ("PAN \uff21\uff22\uff23\uff24\uff251234\uff26", f"PAN {MASK}234\uff26"),
        ("PAN ABCDE\u0967\u0968\u0969\u096aF", f"PAN {MASK}\u0968\u0969\u096aF"),
        # A link between a number's groups goes first, so its groups read as one.
        ("Aadhaar 1234 https://evil.example/x 5678 9012", f"Aadhaar {MASK}9012"),
    ],
)
def test_review_more_ways_round_the_masking(text: str, masked: str) -> None:
    assert mask(text) == (masked, True)


@pytest.mark.parametrize(
    "text",
    [
        "From 01-04-2025 \u2014 31-03-2026",  # a dash with a space on each side: two numbers
        "Paid 50000 \u2212 20000",
        "In March 2026 a bill came",  # five letters, four digits, a letter: not a PAN with spaces
        "\U0001f468\u200d\U0001f469\u200d\U0001f467 2026",  # a family emoji keeps its joiners
        "\u0a95\u0acd\u200d\u0ab7 \u0631\u200c\u06cc",  # Gujarati and Urdu shaping kept
    ],
)
def test_review_what_is_not_an_id_number_is_left_as_it_is(text: str) -> None:
    assert mask(text) == (text, False)


HOME = "https://kaagaz.test"


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # A host with no scheme is made safe, its text kept: Unicode letters, a port, a query, a
        # fragment, bare, an IP address.
        ("Login at k\u0430\u0430gaz.com/login", "Login at k\u0430\u0430gaz[.]com/login"),
        ("b\u00fccher.de/pay", "b\u00fccher[.]de/pay"),
        ("evil.\u0440\u0444/pay", "evil[.]\u0440\u0444/pay"),
        ("evil.com:443/pay now", "evil[.]com:443/pay now"),
        ("evil.com?x=1", "evil[.]com?x=1"),
        ("evil.com#x", "evil[.]com#x"),
        ("Visit evil.com.", "Visit evil[.]com."),
        ("1.2.3.4/x", "1[.]2[.]3[.]4/x"),
        # A scheme, mailto: or www. (even after _): removed.
        ("Mail mailto:x@evil.com now", "Mail  now"),
        ("Go _www.evil.com now", "Go _ now"),
        ("Pay https://evil.example/pay now", "Pay  now"),
        # Kaagaz's own link stays, up to where a link can't go on.
        (f'{HOME}/x"https://evil.example/pay', f'{HOME}/x"'),
        (f"Open {HOME}/documents/1?page=2", f"Open {HOME}/documents/1?page=2"),
        (f"Sign in at {HOME}.evil.example/login", "Sign in at "),  # Kaagaz's address, then another
        # Not links: left alone, or made safe and still read whole.
        ("Ref.No.KMC/123/2024", "Ref[.]No[.]KMC/123/2024"),
        ("Sharma Pvt.Ltd/GST", "Sharma Pvt[.]Ltd/GST"),
        ("FY 2024-25", "FY 2024-25"),
        ("bill 1.pdf and scan.jpeg", "bill 1.pdf and scan.jpeg"),
        ("Total 1,200.50 on 15.11.2026, e.g. v2.0", "Total 1,200.50 on 15.11.2026, e.g. v2.0"),
    ],
)
def test_no_link_but_kaagazs_own_and_a_host_made_safe(text: str, kept: str) -> None:
    assert kaagaz_links_only(text, HOME) == kept
