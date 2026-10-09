"""PRD 5 Q4, V2: the server counts an upload's pages itself, and only a known count of
1 to EXPRESS_MAX_PAGES goes to the express lane. Anything it cannot count goes to the
main lane. The browser's count is never asked for."""

import io
import tempfile

import pytest
from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.ingestion.pagecount import count_pages, goes_express
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from tests.fakes import MemoryRepository, MemoryStorage
from tests.pdf_builder import Page, make_pdf

MB = 1024 * 1024
POLICY = UploadPolicy(
    max_bytes=10 * MB,
    allowed_types=frozenset({sniff.PDF, sniff.PNG, sniff.TIFF, sniff.TEXT}),
    type_limits=limits_by_type(pdf=10 * MB, image=10 * MB, sheet=10 * MB),
)


def tiff(frames: int) -> bytes:
    out = io.BytesIO()
    first, *rest = [Image.new("L", (8, 8), shade) for shade in range(frames)]
    first.save(out, format="TIFF", save_all=True, append_images=rest)
    return out.getvalue()


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, format="PNG")
    return out.getvalue()


class LaneQueue:
    """Notes which lane each job was sent to."""

    def __init__(self) -> None:
        self.sent: list[tuple[Job, bool]] = []

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        self.sent.append((job, express))


def lane_of(data: bytes, express_max_pages: int = 3) -> bool:
    queue = LaneQueue()
    service = UploadService(
        POLICY, MemoryRepository(), MemoryStorage(), queue, express_max_pages=express_max_pages
    )
    service.accept("cust-a", io.BytesIO(data))
    ((_, express),) = queue.sent
    return express


def test_count_pages_reads_pdfs_and_tiffs_and_counts_the_rest_as_one() -> None:
    cases = [
        (make_pdf([Page()] * 4), sniff.PDF, 4),
        (tiff(3), sniff.TIFF, 3),
        (png(), sniff.PNG, 1),
        (b"\xff\xd8\xff" + b"1" * 20, sniff.JPEG, 1),
        (b"a,b\n1,2\n", sniff.TEXT, 1),
        (b"PK\x03\x04" + b"1" * 20, sniff.ZIP, 1),
    ]
    for data, detected, pages in cases:
        file = io.BytesIO(data)
        file.seek(7)
        assert count_pages(file, detected) == pages, detected
        assert file.tell() == 0, detected  # rewound for storage


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data:UserWarning")  # Pillow's note on the broken TIFF
def test_a_damaged_or_locked_file_has_no_count() -> None:
    for data, detected in (
        (b"%PDF-1.7\n" + b"1" * 50, sniff.PDF),
        (make_pdf([Page()], password_protected=True), sniff.PDF),
        (b"II*\x00" + b"1" * 50, sniff.TIFF),
    ):
        file = io.BytesIO(data)
        assert count_pages(file, detected) is None, detected
        assert file.tell() == 0


def test_a_spooled_upload_is_counted_in_place() -> None:
    with tempfile.SpooledTemporaryFile(max_size=64) as spooled:
        spooled.write(make_pdf([Page(), Page()]))
        assert count_pages(spooled, sniff.PDF) == 2
        assert spooled.tell() == 0


def test_goes_express_only_for_a_known_count_up_to_the_limit() -> None:
    assert [goes_express(pages, 3) for pages in (None, 0, 1, 3, 4)] == [False, False, True, True, False]


def test_the_upload_lane_is_the_servers_own_count() -> None:
    assert lane_of(make_pdf([Page()] * 3)) is True
    assert lane_of(make_pdf([Page()] * 4)) is False
    assert lane_of(tiff(2)) is True
    assert lane_of(png()) is True
    assert lane_of(b"%PDF-1.7\n" + b"1" * 50) is False  # cannot be counted: main lane
    assert lane_of(make_pdf([Page()]), express_max_pages=0) is False  # no express lane asked for
