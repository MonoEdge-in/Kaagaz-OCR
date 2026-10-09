"""RapidOCR's text finder and recogniser, shared by the threads of one process (PRD 5 G5, V1, G8).

The Gemini reader pins each line it reads to boxes RapidOCR's text finder
actually found on the page (G5), and the CPU checker reads each value's crop
again with RapidOCR's recogniser (V1). Both run on the CPU, inside the worker,
so ``RapidOcrPool`` keeps that work to ``CPU_WORKERS`` calls at a time per
process, however many pages are read at once (OCR_PAGE_CONCURRENCY, G8).

* ``find_boxes(image)``: detection only, no recognition. Each box the finder
  found, as pixels (x0, y0, x1, y1) of ``image``: the smallest upright box
  around its four corners.
* ``read_crop(image, devanagari=...)``: recognition only, no detection. The
  whole crop is read as one line; "" when nothing is read. With
  ``devanagari``, RapidOCR's Devanagari recogniser reads it (the file the image
  build fetches, ``devanagari_model_path``); when that file is missing the crop
  is not read ("", one warning per pool, code ``ocr_model_missing``).

How the installed rapidocr 3.9.2 is called (checked against the package, council
8 Oct 2026): detection only and recognition only are separate ``RapidOCR``
instances, set by constructor params (``Global.use_rec`` / ``Global.use_det``
false, ``Global.use_cls`` false). They are never call arguments: a call
argument is written onto the instance and stays for every later call. An
instance keeps state between calls, so each of the pool's ``CPU_WORKERS``
slots has its own instances, and a call takes a free slot or waits for one.
A slot builds each instance on its first need, once, one build at a time per
pool (the models are loaded from the files in the image, never downloaded).

``rapidocr`` is imported only when a slot first builds an instance, because it
is an optional extra (``pip install .[ocr]``): the pool itself can be made on a
server without it. Nothing here logs what was read.

``shared_pool(cpu_workers)`` is the process's one pool, so the box pinning and
the checker share the ``CPU_WORKERS`` limit.

The RapidOCR plumbing the full-page reader (``ocr.RapidOcrEngine``) shares is
here too: ``build_rapidocr``, the Devanagari model's file and params.
"""

import contextlib
import functools
import logging
import math
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

# x0, y0, x1, y1 in pixels of an image, top left origin.
PixelBox = tuple[float, float, float, float]
# RapidOCR's Devanagari recogniser (PP-OCRv5 mobile, 7,940,361 bytes, Apache-2.0),
# for pages its default one (PP-OCRv6 small, no Devanagari) reads as other scripts.
DEVANAGARI_REC_MODEL = "devanagari_PP-OCRv5_rec_mobile"

_QUIET = {"Global.log_level": "error"}  # RapidOCR's own logger: errors only, never page text
# The text finder alone: no direction classifier, no recogniser.
DETECT_ONLY: dict[str, Any] = {"Global.use_cls": False, "Global.use_rec": False, **_QUIET}
# The recogniser alone: the whole picture is one line.
RECOGNISE_ONLY: dict[str, Any] = {"Global.use_det": False, "Global.use_cls": False, **_QUIET}

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Slot:
    """One CPU slot's own RapidOCR instances, each built on its first need."""

    finder: Any = None
    reader: Any = None
    devanagari: Any = None


class RapidOcrPool:
    """At most ``cpu_workers`` RapidOCR calls at a time, each on its slot's own instances
    (see the module notes). Thread-safe. A call's time is RapidOCR's, roughly linear in
    the picture's pixels, plus the wait for a free slot."""

    def __init__(self, cpu_workers: int) -> None:
        if cpu_workers < 1:
            raise ValueError("cpu_workers must be at least 1")
        self.size = cpu_workers
        self._free: queue.SimpleQueue[_Slot] = queue.SimpleQueue()
        for _ in range(cpu_workers):
            self._free.put(_Slot())
        self._building = threading.Lock()
        self._missing_told = False

    def find_boxes(self, image: Image.Image) -> list[PixelBox]:
        """Every box RapidOCR's text finder found on ``image`` (G5), in its order (top to
        bottom), in pixels; [] when it found none. A box with no area is left out."""
        picture = image.convert("RGB")
        try:
            with self._slot() as slot:
                if slot.finder is None:
                    slot.finder = self._build(DETECT_ONLY)
                result = slot.finder(picture)
        finally:
            picture.close()
        return _boxes(getattr(result, "boxes", None))

    def read_crop(self, image: Image.Image, *, devanagari: bool) -> str:
        """``image`` read as one line by RapidOCR's recogniser (V1), with its spaces
        collapsed; "" when nothing is read. ``devanagari``: by the Devanagari recogniser
        (see the module notes for a missing model file)."""
        picture = image.convert("RGB")
        try:
            with self._slot() as slot:
                model = self._devanagari(slot) if devanagari else self._reader(slot)
                if model is None:
                    return ""
                result = model(picture)
        finally:
            picture.close()
        texts = getattr(result, "txts", None) or ()
        return " ".join(" ".join(str(text).split()) for text in texts if str(text).strip())

    @contextlib.contextmanager
    def _slot(self) -> Iterator[_Slot]:
        """A free slot, held until the block ends; waits while every slot is busy."""
        slot = self._free.get()
        try:
            yield slot
        finally:
            self._free.put(slot)

    def _build(self, params: dict[str, Any]) -> Any:
        with self._building:  # one model load at a time: the files are large
            return build_rapidocr(params)

    def _reader(self, slot: _Slot) -> Any:
        if slot.reader is None:
            slot.reader = self._build(RECOGNISE_ONLY)
        return slot.reader

    def _devanagari(self, slot: _Slot) -> Any:
        """The slot's Devanagari recogniser, or None when its file is missing."""
        if slot.devanagari is None:
            path = devanagari_model_path()
            if not path.is_file():
                self._tell_missing()
                return None
            slot.devanagari = self._build({**devanagari_params(path), **RECOGNISE_ONLY})
        return slot.devanagari

    def _tell_missing(self) -> None:
        with self._building:
            told, self._missing_told = self._missing_told, True
        if not told:
            _log.warning("ocr model missing, crops not read", extra={"code": "ocr_model_missing"})


def build_rapidocr(params: dict[str, Any]) -> Any:
    """One ``rapidocr.RapidOCR`` with ``params``, imported here on first need: an optional
    extra (see the module notes)."""
    from rapidocr import RapidOCR

    return RapidOCR(params=params)


def devanagari_params(path: Path) -> dict[str, Any]:
    """The Devanagari recogniser's settings. With ``Rec.model_path`` set, RapidOCR loads
    that file and never reaches its download step; the file holds its own letters. The
    other ``Rec`` values name the same model, as Enum values (RapidOCR refuses plain
    strings for them)."""
    from rapidocr import LangRec, ModelType, OCRVersion

    return {
        "Global.log_level": "error",
        "Rec.lang_type": LangRec.DEVANAGARI,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "Rec.model_path": str(path),
    }


def devanagari_model_path() -> Path:
    """Where the image's build puts the Devanagari model: RapidOCR's own models folder,
    in its package (backend/Dockerfile checks the file's SHA-256 there)."""
    import rapidocr

    return Path(str(rapidocr.__file__)).parent / "models" / f"{DEVANAGARI_REC_MODEL}.onnx"


@functools.cache
def shared_pool(cpu_workers: int) -> RapidOcrPool:
    """The process's one pool for ``cpu_workers`` (CPU_WORKERS), made on first need: the
    box pinning (G5) and the checker (V1) share it, so RapidOCR stays at CPU_WORKERS
    threads per process (G8)."""
    return RapidOcrPool(cpu_workers)


def _boxes(quads: Any) -> list[PixelBox]:
    """RapidOCR's four-corner boxes as upright pixel boxes. O(n) for n boxes."""
    if quads is None:
        return []
    boxes: list[PixelBox] = []
    for quad in quads:
        xs = [float(point[0]) for point in quad]
        ys = [float(point[1]) for point in quad]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if all(map(math.isfinite, (x0, y0, x1, y1))) and x0 < x1 and y0 < y1:
            boxes.append((x0, y0, x1, y1))
    return boxes
