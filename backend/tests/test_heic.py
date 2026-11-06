"""HEIC and HEIF photos (#522): told by their ftyp box, decoded by pi-heif under the image
path's own limits, then read as any photo is. A file with several pictures is read by its first;
a damaged one fails in plain words.

pi-heif is in the backend image (pyproject.toml); where it is not installed these tests are
skipped, never passed: run them in the image (docker compose build api, then pytest inside).

Changed on purpose (#522): pi-heif, pillow-heif's decode-only build by the same authors, so the
image carries no GPL x265 encoder. It can't write a HEIC file, so the photos here are made once
(6 Nov 2026) with pillow-heif 1.8.1's encoder, outside the project, at quality 90:
``fixtures/heic/one.heic`` is ``text_like_picture(120, 80)``; ``two.heic`` holds
``text_like_picture(100, 50)`` then ``text_like_picture(40, 30)``; ``turned.heic`` is
``text_like_picture(200, 100)`` with EXIF orientation 6 ("turn 90 degrees clockwise to show")."""

import io
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from kaagaz.api.views import status_message
from kaagaz.ingestion import resolve, sniff
from kaagaz.preparing.pages import _heif_opener, image_pages, page_picture, prepare_pages
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import ReadError
from kaagaz.scanning.gate import DocumentStatus
from tests.test_image_reader import FakeOcr, reader_of
from tests.test_pipeline import CUSTOMER, build, stored_type

pytest.importorskip(
    "pi_heif", reason="pi-heif is not installed here; it is in the backend image, run these there"
)

PIXELS = 1_000_000
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "heic"
BACKEND = Path(__file__).resolve().parents[1]


def heic(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_the_photos_are_decoded_by_pi_heif_which_can_never_write_one() -> None:
    # The licence (#522): pi-heif (BSD-3-Clause; libheif and libde265 LGPL-3.0) decodes only, so
    # Pillow can open a HEIF file but never save one, and pillow-heif (whose wheels carry the
    # GPL x265 encoder) is no dependency.
    _heif_opener()
    assert "HEIF" in Image.OPEN
    assert "HEIF" not in Image.SAVE
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = [dependency.split(">")[0].split("=")[0] for dependency in project["dependencies"]]
    assert "pi-heif" in names and "pillow-heif" not in names
    with pytest.raises(KeyError):
        Image.new("RGB", (8, 8)).save(io.BytesIO(), format="HEIF")


def test_a_heic_photo_is_told_by_its_first_bytes_whatever_its_name() -> None:
    data = heic("one.heic")
    assert sniff.detect_type(data[:4096]) == sniff.HEIC
    assert resolve.resolve_kind(data, sniff.HEIC, "photo.csv") == resolve.HEIC


def test_a_heic_photo_is_read_as_one_rgb_page_by_the_image_reader() -> None:
    ocr = FakeOcr()
    spans = list(reader_of(ocr).read(heic("one.heic")))
    assert [(image.mode, image.size) for image in ocr.seen] == [("RGB", (120, 80))]
    assert [(span.text, span.location.page) for span in spans] == [("TOTAL 500", 1)]


def test_preparing_draws_its_page_and_draws_it_again_the_same() -> None:
    data = heic("one.heic")
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
    assert drawn == [(1, 1, (120, 80))]
    assert [(page.page, page.width, page.height, page.unit) for page in prepared.pages] == [
        (1, 120.0, 80.0, "px")
    ]
    again = page_picture(sniff.HEIC, data, 1, dpi=150, max_image_pixels=PIXELS)
    assert again.size == (120, 80) and again.mode == "RGB"


def test_a_turned_phone_photo_is_turned_upright_once_never_twice() -> None:
    # pi-heif applies the file's own turn and sets EXIF's orientation to 1, so the image
    # path's EXIF step (preparing/pages.py) turns nothing more. turned.heic is 200 x 100 with
    # orientation 6, "rotate 90 degrees clockwise to show".
    pictures = list(image_pages(heic("turned.heic"), max_pages=10, max_image_pixels=PIXELS))
    assert [picture.size for picture in pictures] == [(100, 200)]


def test_a_heic_file_with_several_pictures_is_read_by_its_first_only() -> None:
    data = heic("two.heic")
    _heif_opener()
    with Image.open(io.BytesIO(data)) as opened:
        assert getattr(opened, "n_frames", 1) == 2  # both are in the file
    assert [picture.size for picture in image_pages(data, max_pages=10, max_image_pixels=PIXELS)] == [
        (100, 50)
    ]


def test_a_heic_photo_over_the_pixel_limit_is_refused_before_it_is_decoded() -> None:
    with pytest.raises(ReadError) as refused:
        list(image_pages(heic("one.heic"), max_pages=10, max_image_pixels=9_599))  # 120 x 80 is 9,600
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
    data = damage(heic("one.heic"))
    assert sniff.detect_type(data[:4096]) == sniff.HEIC
    with pytest.raises(ReadError) as refused:
        list(image_pages(data, max_pages=10, max_image_pixels=PIXELS))
    assert refused.value.code == "corrupt_file"
    assert refused.value.__context__ is None  # nothing that quotes the file is chained on


def test_a_damaged_heic_upload_fails_with_words_that_say_what_to_do() -> None:
    system = build(socket=ReaderSocket({resolve.HEIC: reader_of(FakeOcr())}))
    data = heic("one.heic")[:300]
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
        CUSTOMER, io.BytesIO(heic("one.heic")), display_name="IMG_0042.HEIC"
    ).document_id
    system.worker.run_once()
    assert system.statuses.get_status(CUSTOMER, doc) is DocumentStatus.READY
    assert [piece.text for piece in system.store.for_document(CUSTOMER, doc)] == ["TOTAL 500"]
