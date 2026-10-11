"""RapidOCR's text finder and recogniser on a bounded pool (PRD 5 G5, V1, G8:
kaagaz/reading/rapid_pool.py). Most tests put a fake ``rapidocr`` module in place,
so they check how the installed 3.9.2 is called (constructor params, never call
arguments) and the thread limits without loading a model; one runs the real one.
"""

import logging
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

from kaagaz.reading.rapid_pool import DETECT_ONLY, RECOGNISE_ONLY, RapidOcrPool, shared_pool

TEXT = "Sunil Patil 27AABCS1234K1Z5"  # never in a log


class LangRec(Enum):
    DEVANAGARI = "devanagari"


class OCRVersion(Enum):
    PPOCRV5 = "PP-OCRv5"


class ModelType(Enum):
    MOBILE = "mobile"


class FakeRapidOcrModule:
    """A stand-in ``rapidocr`` package: every ``RapidOCR`` built is kept with its params.
    A call takes the picture only, so a call argument (use_det=...) is a TypeError."""

    def __init__(self, folder: pathlib.Path, *, hold: float = 0.0) -> None:
        self.built: list[FakeRapidOCR] = []
        self.hold = hold
        self.lock = threading.Lock()
        self.now = 0
        self.most = 0
        self.module = ModuleType("rapidocr")
        self.module.__file__ = str(folder / "__init__.py")
        self.module.__dict__.update(
            RapidOCR=self.make, LangRec=LangRec, OCRVersion=OCRVersion, ModelType=ModelType
        )
        self.boxes: Any = [
            [[10.0, 20.0], [90.0, 20.0], [90.0, 40.0], [10.0, 40.0]],
            [[30.0, 52.0], [80.0, 50.0], [82.0, 70.0], [32.0, 72.0]],  # a little tilted
            [[5.0, 5.0], [5.0, 5.0], [5.0, 9.0], [5.0, 9.0]],  # no width: left out
        ]
        self.texts: Any = (f"  {TEXT}  ",)

    def make(self, params: dict[str, Any]) -> "FakeRapidOCR":
        model = FakeRapidOCR(self, params)
        self.built.append(model)
        return model

    @contextmanager
    def installed(self) -> Iterator[None]:
        with mock.patch.dict(sys.modules, {"rapidocr": self.module}):
            yield


class FakeRapidOCR:
    def __init__(self, package: FakeRapidOcrModule, params: dict[str, Any]) -> None:
        self.package = package
        self.params = params
        self.pictures: list[tuple[str, tuple[int, int]]] = []
        self.inside = 0
        self.overlapped = False

    def __call__(self, picture: Image.Image) -> SimpleNamespace:
        package = self.package
        with package.lock:
            package.now += 1
            package.most = max(package.most, package.now)
            self.inside += 1
            self.overlapped = self.overlapped or self.inside > 1
        try:
            time.sleep(package.hold)
            self.pictures.append((picture.mode, picture.size))
            if self.params.get("Global.use_rec") is False:
                return SimpleNamespace(boxes=package.boxes, scores=[0.9] * 3)  # TextDetOutput
            return SimpleNamespace(txts=package.texts, scores=(0.9,))  # TextRecOutput
        finally:
            with package.lock:
                package.now -= 1
                self.inside -= 1


class PoolTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        self.models = self.folder / "models"
        self.models.mkdir()
        self.fake = FakeRapidOcrModule(self.folder)
        installed = self.fake.installed()
        installed.__enter__()
        self.addCleanup(installed.__exit__, None, None, None)
        self.page = Image.new("L", (120, 90), 255)

    def test_find_boxes_is_detection_only_by_constructor_params(self) -> None:
        """G5: the text finder alone, set when it is built (council fact-check of 3.9.2)."""
        boxes = RapidOcrPool(1).find_boxes(self.page)
        self.assertEqual(boxes, [(10.0, 20.0, 90.0, 40.0), (30.0, 50.0, 82.0, 72.0)])
        (model,) = self.fake.built
        self.assertEqual(model.params, DETECT_ONLY)
        self.assertEqual(
            DETECT_ONLY, {"Global.use_cls": False, "Global.use_rec": False, "Global.log_level": "error"}
        )
        self.assertEqual(model.pictures, [("RGB", (120, 90))])  # its own RGB copy

    def test_find_boxes_gives_nothing_when_nothing_is_found(self) -> None:
        self.fake.boxes = None  # RapidOCR's empty output when the finder finds nothing
        self.assertEqual(RapidOcrPool(1).find_boxes(self.page), [])

    def test_read_crop_is_recognition_only_and_reads_the_crop_as_one_line(self) -> None:
        """V1: the whole crop is one line; spaces collapsed; "" when nothing is read."""
        pool = RapidOcrPool(1)
        self.assertEqual(pool.read_crop(self.page, devanagari=False), TEXT)
        (model,) = self.fake.built
        self.assertEqual(model.params, RECOGNISE_ONLY)
        self.assertEqual(
            RECOGNISE_ONLY, {"Global.use_det": False, "Global.use_cls": False, "Global.log_level": "error"}
        )
        for nothing in [("",), ("  ",), None, ()]:
            with self.subTest(nothing=nothing):
                self.fake.texts = nothing
                self.assertEqual(pool.read_crop(self.page, devanagari=False), "")
        self.assertEqual(len(self.fake.built), 1)  # built once, reused

    def test_a_devanagari_crop_is_read_by_the_devanagari_recogniser_from_its_file(self) -> None:
        model_file = self.models / "devanagari_PP-OCRv5_rec_mobile.onnx"
        model_file.write_bytes(b"model")
        pool = RapidOcrPool(1)
        self.assertEqual(pool.read_crop(self.page, devanagari=True), TEXT)
        (model,) = self.fake.built
        self.assertEqual(
            model.params,
            {
                "Global.log_level": "error",
                "Rec.lang_type": LangRec.DEVANAGARI,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.model_path": str(model_file),
                "Global.use_det": False,
                "Global.use_cls": False,
            },
        )
        pool.read_crop(self.page, devanagari=False)
        self.assertEqual(len(self.fake.built), 2)  # the default recogniser is its own instance

    def test_a_missing_devanagari_model_reads_nothing_and_says_so_once(self) -> None:
        """V3: no reading for the crop, so the value's reason stays ocr_script."""
        pool = RapidOcrPool(2)
        with self.assertLogs("kaagaz.reading.rapid_pool", logging.WARNING) as logs:
            self.assertEqual(pool.read_crop(self.page, devanagari=True), "")
            self.assertEqual(pool.read_crop(self.page, devanagari=True), "")
        self.assertEqual([record.__dict__.get("code") for record in logs.records], ["ocr_model_missing"])
        self.assertNotIn(TEXT, "".join(logs.output))
        self.assertEqual(self.fake.built, [])

    def test_at_most_cpu_workers_calls_at_once_each_on_its_own_slots_instance(self) -> None:
        """G8: RapidOCR work is limited to CPU_WORKERS threads, and an instance (which keeps
        state between calls) is never used by two threads at once."""
        self.fake.hold = 0.02
        pool = RapidOcrPool(2)
        start = threading.Barrier(8)

        def work(k: int) -> None:
            start.wait(timeout=5)
            if k % 2:
                pool.find_boxes(self.page)
            else:
                pool.read_crop(self.page, devanagari=False)

        threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(self.fake.most, 2)
        kinds = sorted("finder" if model.params == DETECT_ONLY else "reader" for model in self.fake.built)
        self.assertLessEqual(kinds.count("finder"), 2)  # at most one per slot
        self.assertLessEqual(kinds.count("reader"), 2)
        self.assertEqual(sum(len(model.pictures) for model in self.fake.built), 8)
        self.assertFalse(any(model.overlapped for model in self.fake.built))

    def test_each_slot_builds_its_instances_once(self) -> None:
        pool = RapidOcrPool(1)
        for _ in range(3):
            pool.find_boxes(self.page)
            pool.read_crop(self.page, devanagari=False)
        self.assertEqual([model.params for model in self.fake.built], [DETECT_ONLY, RECOGNISE_ONLY])


class LazyAndSharedTest(unittest.TestCase):
    def test_the_pool_is_made_without_importing_rapidocr(self) -> None:
        """An optional extra: a server without it still starts; only a call needs it."""
        with mock.patch.dict(sys.modules, {"rapidocr": None}):
            pool = RapidOcrPool(2)
            self.assertEqual(shared_pool(5).size, 5)
            with self.assertRaises(ImportError):
                pool.find_boxes(Image.new("RGB", (4, 4)))

    def test_one_shared_pool_per_size_in_the_process(self) -> None:
        self.assertIs(shared_pool(2), shared_pool(2))

    def test_a_pool_needs_a_worker(self) -> None:
        with self.assertRaises(ValueError):
            RapidOcrPool(0)


class RealRapidOcrTest(unittest.TestCase):
    """One run of the installed rapidocr: detection only, then recognition only of the crop."""

    def test_the_finder_finds_the_text_and_the_recogniser_reads_its_crop(self) -> None:
        page = Image.new("RGB", (900, 300), "white")
        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default(size=60)
        draw.text((60, 40), "INVOICE 1042", fill="black", font=font)
        left, top, right, bottom = draw.textbbox((60, 40), "INVOICE 1042", font=font)
        pool = RapidOcrPool(1)

        (box,) = pool.find_boxes(page)
        x0, y0, x1, y1 = box
        self.assertTrue(0 <= x0 < x1 <= page.width and 0 <= y0 < y1 <= page.height)
        self.assertTrue(x0 <= (left + right) / 2 <= x1 and y0 <= (top + bottom) / 2 <= y1)
        self.assertLess(x0, left + 10)
        self.assertGreater(x1, right - 10)
        crop = page.crop((round(x0), round(y0), round(x1), round(y1)))
        self.assertEqual(pool.read_crop(crop, devanagari=False).replace(" ", ""), "INVOICE1042")

        blank = Image.new("RGB", (300, 100), "white")
        self.assertEqual(pool.find_boxes(blank), [])
        self.assertEqual(pool.read_crop(blank, devanagari=False), "")


if __name__ == "__main__":
    unittest.main()
