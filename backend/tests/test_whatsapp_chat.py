"""The WhatsApp chat's words and limits (#603 W4), without a database: the language a message is
in, every fixed message in three languages within WhatsApp's limits, ID-number masking, the
numbers a translation must keep, and a list as Meta takes it."""

import pytest

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.channel.chat import BUTTON_CHARS, ROW_CHARS, _cut, _numbers
from kaagaz.channel.texts import LANGUAGE_NAMES, LANGUAGES, MASK, TEXTS, language, mask, say
from tests.whatsapp_fake import NUMBER_ID, TOKEN, running_fake

TAB, LINE, NBSP, DASH = (
    chr(9),
    chr(10),
    chr(0xA0),
    chr(0x2013),
)  # a tab, a new line, a no-break space, an en dash


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is the total of the March bill?", "en"),
        ("मार्च 2026 च्या वीज बिलाची एकूण रक्कम किती आहे?", "mr"),
        ("मार्च के बिजली बिल की कुल रकम क्या है?", "hi"),
        ("वीज बिल", "mr"),  # no word of either: Marathi
        ("Bill ka total kya hai", "en"),  # typed in English letters
        ("2026", None),
        ("", None),
    ],
)
def test_the_language_of_a_message(text: str, expected: str | None) -> None:
    assert language(text) == expected


def test_every_fixed_message_is_in_three_languages_within_whatsapps_limits() -> None:
    assert all(len(texts) == len(LANGUAGES) and all(texts) for texts in TEXTS.values())
    for key in (
        "ask_button",
        "get_button",
        "more_button",
        "menu_button",
        "another_button",
        "not_this_button",
    ):
        assert all(len(say(key, lang)) <= BUTTON_CHARS for lang in LANGUAGES), key
    for key in ("see_files", "choose"):  # a list's button
        assert all(len(say(key, lang)) <= BUTTON_CHARS for lang in LANGUAGES), key
    for key in ("help_row", "disconnect_row", "something_else", "all_of_these"):
        assert all(len(say(key, lang)) <= ROW_CHARS for lang in LANGUAGES), key
    assert all(len(name) <= ROW_CHARS for name in LANGUAGE_NAMES.values())


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Aadhaar 1234 5678 9012.", f"Aadhaar {MASK}9012."),
        ("Aadhaar १२३४ ५६७८ ९०१२", f"Aadhaar {MASK}९०१२"),
        ("PAN abcde1234f here", f"PAN {MASK}234F here"),
        ("Card 4111-1111-1111-1111", f"Card {MASK}1111"),
        ("Account 001234567890 at SBI", f"Account {MASK}7890 at SBI"),
        ("Phone +91 98765 43210", f"Phone +{MASK}3210"),
        # Review (#603): no upper bound, and up to 3 of any space, hyphen or en dash between groups.
        ("Ref 12345678901234567890 ok", f"Ref {MASK}7890 ok"),
        ("Aadhaar 1234  5678  9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234" + TAB + "5678" + TAB + "9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234" + LINE + "5678" + LINE + "9012", f"Aadhaar {MASK}9012"),
        ("Aadhaar 1234" + NBSP + "5678" + NBSP + "9012", f"Aadhaar {MASK}9012"),
        ("Card 4111" + DASH + "1111" + DASH + "1111" + DASH + "1111", f"Card {MASK}1111"),
        ("Aadhaar_123456789012.pdf", f"Aadhaar_{MASK}9012.pdf"),
    ],
)
def test_id_and_account_numbers_are_masked(text: str, masked: str) -> None:
    assert mask(text) == (masked, True)


@pytest.mark.parametrize(
    "text",
    [
        "Total ₹18,50,000 on 14 June 2019",
        "Survey No. 45/2, invoice 12345678",  # 8 digits: no ID or account
        "Dated 2019-06-14, 12 pages",
        "ABCDE12345F and XABCDE1234F",  # not a PAN's shape
    ],
)
def test_amounts_dates_and_short_numbers_are_left_alone(text: str) -> None:
    assert mask(text) == (text, False)


def test_a_translation_keeps_the_numbers_in_any_script() -> None:
    assert _numbers("The total is ₹4,820 for March 2026.") == _numbers("एकूण रक्कम ₹४,८२० मार्च २०२६.")
    assert _numbers("The total is 4,820.") != _numbers("एकूण रक्कम 4,800.")
    assert _numbers("Page 01") == _numbers("पान 1")
    assert _numbers("₹4,820 and 18,50,000") == _numbers("₹4820 आणि 1,850,000")  # groupings alike
    assert _numbers("4,820.50") != _numbers("4,820")  # decimals kept


def test_a_cut_never_splits_a_devanagari_letter() -> None:
    assert _cut("नमस्कार", 4) == "नम…"  # not "नमस" without its virama
    assert _cut("रक्कम", 4) == "र…"  # not half of क्क
    assert _cut("Electricity bill", 8) == "Electri…"


def test_a_list_is_one_interactive_message_with_its_rows() -> None:
    with running_fake() as fake:
        MetaWhatsApp(fake.url, NUMBER_ID, TOKEN).send_list(
            "+15550001234",
            "Pick one:",
            "See files",
            [("file:a:en", "a.pdf", "Bills"), ("file:b:en", "b.pdf", "")],
        )
        ((_, _, body),) = fake.requests
    assert body["to"] == "15550001234" and body["type"] == "interactive"
    assert body["interactive"] == {
        "type": "list",
        "body": {"text": "Pick one:"},
        "action": {
            "button": "See files",
            "sections": [
                {
                    "rows": [
                        {"id": "file:a:en", "title": "a.pdf", "description": "Bills"},
                        {"id": "file:b:en", "title": "b.pdf"},
                    ]
                }
            ],
        },
    }
