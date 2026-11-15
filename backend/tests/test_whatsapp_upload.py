"""Kaagaz on WhatsApp's uploads without a database (#603 W5): photos joined into one PDF without
decoding them, Meta's media calls on the fake WhatsApp (an address that expires, a file gone, an
address that is not Meta's kind, the typing indicator), a document's file name kept, the language of
a tap, and every new fixed word within WhatsApp's limits."""

import io
from datetime import UTC, datetime, timedelta

import pypdfium2 as pdfium
import pytest
from PIL import Image

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError
from kaagaz.channel.chat import BUTTON_CHARS, ROW_CHARS
from kaagaz.channel.inbound import messages
from kaagaz.channel.loop import _language_of, notice_rest
from kaagaz.channel.photos import A4_SHORT_POINTS, PhotosPdf, jpeg_size
from kaagaz.channel.texts import LANGUAGES, TEXTS, say
from kaagaz.db.whatsapp import Waiting
from tests.whatsapp_fake import NUMBER_ID, TOKEN, delivery, file_message, running_fake

NOW = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _jpeg(width: int, height: int, mode: str = "RGB", **options: object) -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), "white" if mode != "CMYK" else (0, 0, 0, 0)).save(out, "JPEG", **options)
    return out.getvalue()


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, "PNG")
    return out.getvalue()


# --- photos into one document ------------------------------------------------------------------


def test_a_jpegs_size_and_colours_come_from_its_header_only() -> None:
    assert jpeg_size(_jpeg(40, 20)) == (40, 20, 3)
    assert jpeg_size(_jpeg(7, 9, "L")) == (7, 9, 1)
    assert jpeg_size(_jpeg(30, 10, progressive=True)) == (30, 10, 3)
    for refused in (_png(), _jpeg(8, 8, "CMYK"), b"", b"\xff\xd8", _jpeg(40, 20)[:20], b"%PDF-1.7"):
        assert jpeg_size(refused) is None


def test_photos_become_one_pdf_a_page_each_in_order_at_a4s_short_side() -> None:
    out = io.BytesIO()
    writer = PhotosPdf(out)
    first, second = _jpeg(400, 200), _jpeg(100, 300, "L")
    assert writer.add(first) and writer.add(second)
    writer.close()
    data = out.getvalue()
    assert first in data and second in data  # carried as they came, never decoded
    pdf = pdfium.PdfDocument(data)
    try:
        sizes = [pdf[n].get_size() for n in range(len(pdf))]
    finally:
        pdf.close()
    assert len(sizes) == 2
    (wide, high), (narrow, tall) = sizes
    assert high == pytest.approx(A4_SHORT_POINTS, abs=0.01) and wide == pytest.approx(2 * high, abs=0.02)
    assert narrow == pytest.approx(A4_SHORT_POINTS, abs=0.01) and tall == pytest.approx(3 * narrow, abs=0.02)


def test_a_photo_that_cant_be_carried_as_it_is_adds_nothing() -> None:
    out = io.BytesIO()
    writer = PhotosPdf(out)
    before = out.getvalue()
    assert not writer.add(_png())
    assert out.getvalue() == before and writer.pages == 0


# --- Meta's media calls, on the fake WhatsApp -----------------------------------------------------


def test_a_file_is_found_by_its_media_id_then_read_from_the_address_meta_gives() -> None:
    with running_fake() as fake:
        meta = MetaWhatsApp(fake.url, NUMBER_ID, TOKEN)
        media_id = fake.add_media(b"%PDF-1.7\nhello")
        found = meta.media(media_id)
        assert found.size == len(b"%PDF-1.7\nhello")
        with meta.download(found.url) as stream:
            assert stream.read(100) == b"%PDF-1.7\nhello"
        assert fake.fetched == [media_id]


def test_a_file_meta_no_longer_has_or_an_old_address_is_refused() -> None:
    with running_fake() as fake:
        meta = MetaWhatsApp(fake.url, NUMBER_ID, TOKEN)
        media_id = fake.add_media(b"x")
        fake.gone.add(media_id)
        with pytest.raises(WhatsAppError) as gone:
            meta.media(media_id)
        assert (gone.value.code, gone.value.status) == ("refused", 404)
        fake.gone.clear()
        fake.media_seconds = 0.0  # the address expires at once (Meta's last 5 minutes)
        found = meta.media(media_id)
        with pytest.raises(WhatsAppError) as expired, meta.download(found.url):
            pass
        assert expired.value.status == 404
        assert fake.fetched == []


def test_only_an_address_of_meta_s_kind_is_read() -> None:
    meta = MetaWhatsApp("https://graph.facebook.com/v21.0", NUMBER_ID, TOKEN)
    assert meta._meta_address("https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1")
    for refused in (
        "http://lookaside.fbsbx.com/x",  # never plain http while the Graph API is https
        "https://user:pass@lookaside.fbsbx.com/x",
        "https://lookaside.fbsbx.com/x#part",
        "file:///etc/passwd",
        "ftp://x/y",
        "https://evil.example/x",  # any other host: the access token never goes there
        "https://lookaside.fbsbx.com.evil.example/x",
        "https://notfbsbx.com/x",
    ):
        assert not meta._meta_address(refused), refused
    with pytest.raises(WhatsAppError), meta.download("http://lookaside.fbsbx.com/x"):
        pass
    assert MetaWhatsApp("http://127.0.0.1:9/v0.0", NUMBER_ID, TOKEN)._meta_address(
        "http://127.0.0.1:9/media/x"
    )


def test_a_media_address_on_the_graph_apis_own_host_or_metas_is_read() -> None:
    meta = MetaWhatsApp("https://graph.facebook.com/v21.0", NUMBER_ID, TOKEN)
    for allowed in (
        "https://graph.facebook.com/media/1",
        "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
        "https://scontent.whatsapp.facebook.com/x",
    ):
        assert meta._meta_address(allowed), allowed
    loopback = MetaWhatsApp("http://127.0.0.1:9/v0.0", NUMBER_ID, TOKEN)
    assert not loopback._meta_address("http://127.0.0.2:9/media/x")  # another loopback host: no


@pytest.mark.parametrize(("age", "rest"), [(0, 3), (20, 5), (100, 13), (3600, 60), (-5, 3)])
def test_a_notice_rests_longer_as_it_ages_to_a_minute(age: int, rest: int) -> None:
    assert notice_rest(timedelta(seconds=age)) == rest


def test_the_typing_indicator_marks_the_message_read_and_is_no_message() -> None:
    with running_fake() as fake:
        fake.failures = [500]  # kept for the next message, never spent on the indicator
        MetaWhatsApp(fake.url, NUMBER_ID, TOKEN).typing("wamid.abc")
        assert fake.typing == ["wamid.abc"] and fake.sent() == [] and fake.failures == [500]


# --- what comes in --------------------------------------------------------------------------------


def test_a_documents_file_name_is_kept_and_a_photo_has_none() -> None:
    document = file_message("+15551234567", NOW, "11", filename="Invoice_1123.pdf")
    photo = file_message("+15551234567", NOW, "12", kind="image")
    found = messages(delivery(document, photo), NUMBER_ID)
    assert [(m.kind, m.body, m.media_id) for m in found] == [
        ("document", "Invoice_1123.pdf", "11"),
        ("image", None, "12"),
    ]


@pytest.mark.parametrize(
    ("kind", "body", "lang"),
    [
        ("text", "मार्च च्या बिलाची रक्कम काय आहे?", "mr"),
        ("interactive", "ask.hi", "hi"),
        ("interactive", "up:f:3:ab12:mr", "mr"),
        ("interactive", "up:save:3::xx", "en"),
        ("image", None, "en"),
    ],
)
def test_looking_is_said_in_the_language_of_the_message_or_tap(
    kind: str, body: str | None, lang: str
) -> None:
    assert _language_of(Waiting("m", kind, body, NOW)) == lang


def test_every_new_button_and_row_is_within_whatsapps_limits() -> None:
    assert all(len(texts) == len(LANGUAGES) and all(texts) for texts in TEXTS.values())
    for key in TEXTS:
        most = BUTTON_CHARS if key.endswith("_button") else ROW_CHARS if key.endswith("_row") else None
        if most is not None:
            assert all(len(say(key, lang)) <= most for lang in LANGUAGES), key
    assert all(len(say("auto_button", lang)) <= ROW_CHARS for lang in LANGUAGES)  # a row too
