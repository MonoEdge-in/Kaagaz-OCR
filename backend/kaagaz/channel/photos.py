"""Photos sent on WhatsApp as one document (#603 W5, PRD 7 section 15: "Several photos ... [One
document]"): a PDF of one page a photo, in the order sent.

Nothing is decoded: the photo has not been virus scanned yet (the worker scans the PDF, as every
upload), so only its JPEG frame header is read here, in plain Python, for its width, height and
colours, and its bytes go into the PDF as they came (``/DCTDecode``, which every PDF reader decodes
itself). A photo that is not a baseline or progressive JPEG of 1 or 3 colours at 8 bits is not
joined (``add`` says False), and the photos are then saved one by one.

Each page's short side is A4's (595.28 points), so the reader measures the same resolution as for
the photo uploaded on its own (``preparing/page_checks.py``). Written as it goes: one photo at a
time is held. O(total bytes).
"""

from typing import IO

A4_SHORT_POINTS = 595.28
_JOINED_KINDS = frozenset({0xC0, 0xC1, 0xC2})  # baseline, extended and progressive DCT frames
_COLOURS = {1: "/DeviceGray", 3: "/DeviceRGB"}


def jpeg_size(data: bytes) -> tuple[int, int, int] | None:
    """(width, height, colours) from a JPEG's frame header; None when it is not a JPEG the PDF can
    carry as it is. Walks the markers before the frame only: O(header)."""
    if data[:2] != b"\xff\xd8":
        return None
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:  # fill
            at += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # no length
            at += 2
            continue
        length = int.from_bytes(data[at + 2 : at + 4], "big")
        if length < 2 or marker == 0xDA:  # a scan before any frame
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if marker not in _JOINED_KINDS or length < 8 or at + 10 > len(data):
                return None
            precision, colours = data[at + 4], data[at + 9]
            height = int.from_bytes(data[at + 5 : at + 7], "big")
            width = int.from_bytes(data[at + 7 : at + 9], "big")
            if precision != 8 or colours not in _COLOURS or not width or not height:
                return None
            return width, height, colours
        at += 2 + length
    return None


class PhotosPdf:
    """A PDF written into ``into`` as photos are added; ``close`` ends it."""

    def __init__(self, into: IO[bytes]) -> None:
        self._into = into
        self._written = 0
        self._offsets: dict[int, int] = {}
        self._pages: list[int] = []
        self._write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        self._object(1, b"<< /Type /Catalog /Pages 2 0 R >>")  # 2: the page list, written last

    def add(self, photo: bytes) -> bool:
        """``photo`` as the next page; False (nothing written) when it can't be carried as it is."""
        found = jpeg_size(photo)
        if found is None:
            return False
        width, height, colours = found
        scale = A4_SHORT_POINTS / min(width, height)
        wide, high = width * scale, height * scale
        image, content, page = (3 + 3 * len(self._pages) + n for n in range(3))
        head = (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height}"
            f" /ColorSpace {_COLOURS[colours]} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(photo)} >>"
        )
        self._object(image, head.encode("ascii") + b"\nstream\n" + photo + b"\nendstream")
        draw = f"q {wide:.2f} 0 0 {high:.2f} 0 0 cm /Im0 Do Q".encode("ascii")
        self._object(content, b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
        self._object(
            page,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {wide:.2f} {high:.2f}]"
                f" /Resources << /XObject << /Im0 {image} 0 R >> >> /Contents {content} 0 R >>"
            ).encode("ascii"),
        )
        self._pages.append(page)
        return True

    @property
    def pages(self) -> int:
        return len(self._pages)

    @property
    def size(self) -> int:
        """The bytes written so far."""
        return self._written

    def close(self) -> None:
        kids = " ".join(f"{page} 0 R" for page in self._pages)
        self._object(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(self._pages)} >>".encode("ascii"))
        start, size = self._written, max(self._offsets) + 1
        rows = [b"0000000000 65535 f \n"] + [b"%010d 00000 n \n" % self._offsets[n] for n in range(1, size)]
        self._write(b"xref\n0 %d\n" % size + b"".join(rows))
        self._write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start))

    def _object(self, number: int, body: bytes) -> None:
        self._offsets[number] = self._written
        self._write(b"%d 0 obj\n" % number + body + b"\nendobj\n")

    def _write(self, data: bytes) -> None:
        self._into.write(data)
        self._written += len(data)
