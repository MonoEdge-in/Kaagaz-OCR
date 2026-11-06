"""HEIC and HEIF photos (#522): told by their ftyp box, decoded by pillow-heif under the image
path's own limits, then read as any photo is. A file with several pictures is read by its first;
a damaged one fails in plain words.

pillow-heif is in the backend image (pyproject.toml); where it is not installed these tests are
skipped, never passed: run them in the image (docker compose build api, then pytest inside)."""

import io
from collections.abc import Callable

import pytest
from PIL import Image

from kaagaz.api.views import status_message
from kaagaz.ingestion import resolve, sniff
from kaagaz.preparing.pages import image_pages, page_picture, prepare_pages
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import ReadError
from kaagaz.scanning.gate import DocumentStatus
from tests.picture_support import text_like_picture
from tests.test_image_reader import FakeOcr, reader_of
from tests.test_pipeline import CUSTOMER, build, stored_type

pillow_heif = pytest.importorskip(
    "pillow_heif", reason="pillow-heif is not installed here; it is in the backend image, run these there"
)

PIXELS = 1_000_000


def heic(*pictures: Image.Image) -> bytes:
    """``pictures`` in one HEIC file, the first its main picture."""
    file = pillow_heif.from_pillow(pictures[0])
    for picture in pictures[1:]:
        file.add_from_pillow(picture)
    buffer = io.BytesIO()
    file.save(buffer, quality=90)
    return buffer.getvalue()


def test_a_heic_photo_is_told_by_its_first_bytes_whatever_its_name() -> None:
    data = heic(text_like_picture(64, 48))
    assert sniff.detect_type(data[:4096]) == sniff.HEIC
    assert resolve.resolve_kind(data, sniff.HEIC, "photo.csv") == resolve.HEIC


def test_a_heic_photo_is_read_as_one_rgb_page_by_the_image_reader() -> None:
    ocr = FakeOcr()
    spans = list(reader_of(ocr).read(heic(text_like_picture(120, 80, mode="RGB"))))
    assert [(image.mode, image.size) for image in ocr.seen] == [("RGB", (120, 80))]
    assert [(span.text, span.location.page) for span in spans] == [("TOTAL 500", 1)]


def test_preparing_draws_its_page_and_draws_it_again_the_same() -> None:
    data = heic(text_like_picture(90, 60, mode="RGB"))
    drawn: list[tuple[int, int, tuple[int, int]]] = []
    prepared = prepare_pages(
        sniff.HEIC,
        data,
        lambda page, count, picture: drawn.append((page, count, picture.size)),
        dpi=150,
        max_pages=10,
        max_image_pixels=PIXELS,
        blank_stddev=3.0,
        low_res_dpi=150,
    )
    assert drawn == [(1, 1, (90, 60))]
    assert [(page.page, page.width, page.height, page.unit) for page in prepared.pages] == [
        (1, 90.0, 60.0, "px")
    ]
    again = page_picture(sniff.HEIC, data, 1, dpi=150, max_image_pixels=PIXELS)
    assert again.size == (90, 60) and again.mode == "RGB"


def test_a_turned_phone_photo_is_turned_upright_once_never_twice() -> None:
    # pillow-heif applies the file's own turn and sets EXIF's orientation to 1, so the image
    # path's EXIF step (preparing/pages.py) turns nothing more.
    pillow_heif.register_heif_opener()
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to show"
    buffer = io.BytesIO()
    text_like_picture(200, 100, mode="RGB").save(buffer, format="HEIF", exif=exif.tobytes())
    pictures = list(image_pages(buffer.getvalue(), max_pages=10, max_image_pixels=PIXELS))
    assert [picture.size for picture in pictures] == [(100, 200)]


def test_a_heic_file_with_several_pictures_is_read_by_its_first_only() -> None:
    data = heic(text_like_picture(100, 50, mode="RGB"), text_like_picture(40, 30, mode="RGB"))
    with Image.open(io.BytesIO(data)) as opened:
        assert getattr(opened, "n_frames", 1) == 2  # both are in the file
    assert [picture.size for picture in image_pages(data, max_pages=10, max_image_pixels=PIXELS)] == [
        (100, 50)
    ]


def test_a_heic_photo_over_the_pixel_limit_is_refused_before_it_is_decoded() -> None:
    with pytest.raises(ReadError) as refused:
        list(image_pages(heic(text_like_picture(200, 100)), max_pages=10, max_image_pixels=19_999))
    assert refused.value.code == "image_too_large"


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: data[: len(data) // 2],  # cut short
        lambda data: data[:200],  # its boxes cut off
        lambda data: data[:32] + b"\x00" * (len(data) - 32),  # everything after the ftyp box gone
    ],
)
def test_a_damaged_heic_is_corrupt_in_plain_words(damage: Callable[[bytes], bytes]) -> None:
    data = damage(heic(text_like_picture(64, 48)))
    assert sniff.detect_type(data[:4096]) == sniff.HEIC
    with pytest.raises(ReadError) as refused:
        list(image_pages(data, max_pages=10, max_image_pixels=PIXELS))
    assert refused.value.code == "corrupt_file"
    assert refused.value.__context__ is None  # nothing that quotes the file is chained on


def test_a_damaged_heic_upload_fails_with_words_that_say_what_to_do() -> None:
    system = build(socket=ReaderSocket({resolve.HEIC: reader_of(FakeOcr())}))
    data = heic(text_like_picture(64, 48))[:300]
    doc = system.upload.accept(CUSTOMER, io.BytesIO(data), display_name="IMG_0042.HEIC").document_id
    system.worker.run_once()
    assert stored_type(system, doc) == resolve.HEIC
    assert system.statuses.get_status(CUSTOMER, doc) is DocumentStatus.FAILED
    assert system.repo.failure_codes[(CUSTOMER, doc)] == "corrupt_file"
    assert status_message("failed", "corrupt_file", detected_type="heic", max_pages=100) == (
        "This file is damaged, so it can't be opened. Save it again and upload that copy."
    )


def test_a_good_heic_upload_is_read_and_ready() -> None:
    system = build(socket=ReaderSocket({resolve.HEIC: reader_of(FakeOcr())}))
    doc = system.upload.accept(
        CUSTOMER, io.BytesIO(heic(text_like_picture(64, 48))), display_name="IMG_0042.HEIC"
    ).document_id
    system.worker.run_once()
    assert system.statuses.get_status(CUSTOMER, doc) is DocumentStatus.READY
    assert [piece.text for piece in system.store.for_document(CUSTOMER, doc)] == ["TOTAL 500"]
