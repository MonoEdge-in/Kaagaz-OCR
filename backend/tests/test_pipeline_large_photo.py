"""PRD 1 section 11: "A photo that is 19 MB but 50 megapixels: accepted at upload,
then failed in the worker with image_too_large."

End to end with the laptop's real limits (.env.example): photos up to 20 MB
(``MAX_IMAGE_MB``) and pictures up to 40 million pixels (``MAX_IMAGE_PIXELS``).
The real upload service, sniffing, page preparer and worker loop run over the
in-memory stores (tests/fakes.py).

The photo is a real PNG of 10,000 by 5,000 grey pixels, written row by row
straight to a temporary file, so memory holds one row at a time while it is
made. Its first rows are random bytes, which do not compress, so the file is
about 19 MB; the other rows are blank. The worker refuses it from its header,
without decoding the pixels.
"""

import functools
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import BinaryIO

from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import Worker
from kaagaz.pipeline import DocumentProcessor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from tests.fakes import (
    FakeScanner,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryStatuses,
    MemoryStorage,
    PresetSocket,
    fake_sheet_pages,
)
from tests.lease_queue import FakeClock, LeaseQueue

MIB = 1024 * 1024
CUSTOMER = "cust-a"
WIDTH, HEIGHT = 10_000, 5_000  # 50 megapixels
NOISY_ROWS = 1_950  # about 19 MB of bytes that do not compress

# The shipped limits (.env.example): MAX_UPLOAD_MB, MAX_IMAGE_MB, MAX_SHEET_MB, ALLOWED_TYPES.
POLICY = UploadPolicy(
    max_bytes=50 * MIB,
    allowed_types=frozenset({"pdf", "png", "jpeg", "tiff", "zip", "text"}),
    type_limits=limits_by_type(pdf=50 * MIB, image=20 * MIB, sheet=10 * MIB),
)
PREPARE = functools.partial(prepare_pages, dpi=150, max_pages=100, max_image_pixels=40_000_000)


def _chunk(out: BinaryIO, kind: bytes, data: bytes) -> None:
    out.write(struct.pack(">I", len(data)) + kind + data)
    out.write(struct.pack(">I", zlib.crc32(kind + data)))


def write_photo(path: Path) -> None:
    """A valid 8-bit grey PNG, one row in memory at a time. O(pixels) time, O(width) memory."""
    blank = bytes(WIDTH + 1)  # filter byte 0, then black pixels
    squeeze = zlib.compressobj(level=1)
    with path.open("wb") as out:
        out.write(b"\x89PNG\r\n\x1a\n")
        _chunk(out, b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 0, 0, 0, 0))
        for row in range(HEIGHT):
            line = b"\x00" + os.urandom(WIDTH) if row < NOISY_ROWS else blank
            packed = squeeze.compress(line)
            if packed:
                _chunk(out, b"IDAT", packed)
        _chunk(out, b"IDAT", squeeze.flush())
        _chunk(out, b"IEND", b"")


class LargePhotoTest(unittest.TestCase):
    def test_a_19_mb_50_megapixel_photo_is_accepted_then_fails_with_image_too_large(self) -> None:
        clock = FakeClock()
        queue = LeaseQueue(clock, lease_seconds=30, max_attempts=3)
        statuses, storage, page_images = MemoryStatuses(), MemoryStorage(), MemoryPageImages()
        documents = MemoryDocuments(statuses)
        processor = DocumentProcessor(
            gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, statuses),
            statuses=statuses,
            documents=documents,
            socket=PresetSocket({}),
            prepare=PREPARE,
            sheet_pages=fake_sheet_pages,
            page_images=page_images,
            store=MemoryPipelineStore(),
        )
        worker = Worker(queue, processor, max_attempts=2, on_exhausted=lambda job: None)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.png"
            write_photo(path)
            size = path.stat().st_size
            self.assertTrue(18 * MIB < size <= 19 * MIB, size)  # "19 MB", rounded up as the messages do
            with path.open("rb") as photo:
                result = UploadService(POLICY, documents, storage, queue).accept(CUSTOMER, photo)

        self.assertFalse(result.duplicate)
        record = documents.get(CUSTOMER, result.document_id)
        assert record is not None
        self.assertEqual((record.detected_type, record.size), (sniff.PNG, size))

        self.assertTrue(worker.run_once())
        key = (CUSTOMER, result.document_id)
        self.assertIs(statuses.get_status(*key), DocumentStatus.FAILED)
        self.assertEqual(documents.failure_codes[key], "image_too_large")
        self.assertEqual([failed.code for failed in queue.failed.values()], ["image_too_large"])
        self.assertEqual(page_images.images, {})
