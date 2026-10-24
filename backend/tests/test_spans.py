"""The optional layout fields on spans and OCR lines (PRD 2 P3, P4), and how a
span is stored as JSON.

The new JSON keys are written only when set, so a span from a reader that sets
none of them is stored exactly as before, and every output stored before
PRD 2 still loads.
"""

import dataclasses
import json
import unittest

from kaagaz.db import pipeline_store
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading import ocr, spans
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.spans import BoxLocation, CellLocation, Span, location_to_json

BOX = BoxLocation(2, 0.125, 0.25, 0.5, 0.375)
NEW_KEYS = ("kind", "origin", "table_id", "row", "col", "header")


class SpanFieldsTest(unittest.TestCase):
    def test_the_block_kinds_and_origins_are_the_prds(self) -> None:
        self.assertEqual(
            spans.BLOCK_KINDS,
            frozenset(
                {
                    "title",
                    "heading",
                    "paragraph",
                    "list_item",
                    "table_cell",
                    "header",
                    "footer",
                    "stamp",
                    "signature",
                    "image",
                    "formula",
                    "other",
                }
            ),
        )
        self.assertEqual(spans.ORIGINS, frozenset({"cell", "text_layer", "ocr"}))

    def test_the_word_marks_are_the_prds(self) -> None:
        """PRD 5 F3: quality ok, unsure or illegible; box_source found or model (G5)."""
        self.assertEqual(spans.QUALITIES, frozenset({"ok", "unsure", "illegible"}))
        self.assertEqual(spans.BOX_SOURCES, frozenset({"found", "model"}))
        span = Span("Total 500", "Total 500", BOX, origin="ocr")
        self.assertEqual((span.quality, span.box_source), (None, None))
        names = [field.name for field in dataclasses.fields(Span)]
        self.assertEqual(names[-2:], ["quality", "box_source"])  # after the PRD 2 fields

    def test_the_new_fields_default_to_none(self) -> None:
        span = Span("Total 500", "Total 500", BOX)
        self.assertEqual(
            (span.kind, span.origin, span.table_id, span.row, span.col, span.header), (None,) * 6
        )


class SpanJsonTest(unittest.TestCase):
    def test_span_json_writes_kind_origin_table_only_when_set_and_old_outputs_load(self) -> None:
        plain = Span("Total 500", " Total 500 ", BOX, 0.9)
        self.assertEqual(
            span_to_json(plain),
            {
                "text": "Total 500",
                "raw": " Total 500 ",
                "region": {"kind": "box", "page": 2, "box": [0.125, 0.25, 0.5, 0.375]},
                "confidence": 0.9,
            },
        )

        cell = Span(
            "7,000.00", "7,000.00", BOX, None, "table_cell", "ocr", table_id="t1", row=3, col=4, header=False
        )
        data = span_to_json(cell)
        self.assertEqual(
            {key: data[key] for key in NEW_KEYS},
            {"kind": "table_cell", "origin": "ocr", "table_id": "t1", "row": 3, "col": 4, "header": False},
        )

        # Only the keys that are set: a direct PDF line has an origin and nothing else.
        line = span_to_json(Span("typed", "typed", BOX, origin="text_layer"))
        self.assertEqual([key for key in NEW_KEYS if key in line], ["origin"])

        # An output stored before PRD 2 (PRD 1's shape) loads with every new field None.
        old = {"text": "500", "raw": "500", "region": {"kind": "cell", "page": 1, "row": 4, "column": 2}}
        loaded = pipeline_store.span_from_json({**old, "confidence": None})
        self.assertEqual(loaded, Span("500", "500", CellLocation(1, 4, 2)))

    def test_span_json_writes_the_word_marks_only_when_set(self) -> None:
        """PRD 5 F3: like kind and origin, so every output stored before still loads."""
        marked = span_to_json(Span("Patil", "Patil", BOX, None, "paragraph", "ocr", quality="unsure"))
        self.assertEqual((marked["quality"], "box_source" in marked), ("unsure", False))
        pinned = span_to_json(Span("Total 500", "Total 500", BOX, origin="ocr", box_source="found"))
        self.assertEqual(("quality" in pinned, pinned["box_source"]), (False, "found"))
        plain = span_to_json(Span("Total 500", "Total 500", BOX, origin="ocr"))
        self.assertEqual([key for key in ("quality", "box_source") if key in plain], [])
        old = {"text": "500", "raw": "500", "region": location_to_json(BOX), "confidence": None}
        loaded = pipeline_store.span_from_json({**old, "origin": "ocr"})
        self.assertEqual((loaded.quality, loaded.box_source), (None, None))

    def test_every_field_survives_a_round_trip_through_json_text(self) -> None:
        for span in (
            Span("Tax Invoice", "Tax Invoice", BOX, None, "heading", "ocr"),
            Span("Qty", "Qty", BOX, None, "table_cell", "ocr", table_id="t2", row=0, col=1, header=True),
            Span("500", "500", CellLocation(1, 4, 2), origin="cell"),
            Span("scan", "scan", BOX, 0.75),
            Span("[?]", "[?]", BOX, None, "paragraph", "ocr", quality="illegible", box_source="model"),
            Span("7/12", "7/12", BOX, None, "heading", "ocr", quality="ok", box_source="found"),
        ):
            with self.subTest(span=span):
                stored = json.loads(json.dumps(span_to_json(span)))
                self.assertEqual(pipeline_store.span_from_json(stored), span)

    def test_a_table_row_never_collides_with_a_cells_row(self) -> None:
        # ``row`` and ``col`` are top-level keys; a cell's own row stays inside ``region``,
        # which is all listing.sheet_cells reads.
        data = span_to_json(Span("500", "500", CellLocation(1, 4, 2), origin="cell"))
        self.assertEqual(data["region"]["row"], 4)
        self.assertNotIn("row", data)


class ExtractedValueOriginTest(unittest.TestCase):
    def test_origin_defaults_to_none_and_follows_label_location(self) -> None:
        value = ExtractedValue("document", "total_amount", "500", "500", BOX)
        self.assertIsNone(value.origin)
        # AR16: ..., value_date, label_location (PR 1), origin (PR 2), then quote (PR 5).
        # Changed on purpose (features 25-27, 15 Oct 2026): then votes (Ensemble's vote).
        # Changed on purpose (0054, 24 Oct 2026): then label_text (a map value's label).
        names = [field.name for field in dataclasses.fields(ExtractedValue)]
        self.assertEqual(names[-5:], ["label_location", "origin", "quote", "votes", "label_text"])
        self.assertLess(names.index("value_date"), names.index("origin"))


class OcrLineFieldsTest(unittest.TestCase):
    def test_kind_and_table_are_optional_and_confidence_may_be_none(self) -> None:
        line = OcrLine("Sunil Patil", 192, 718, 432, 781, None)
        self.assertEqual((line.confidence, line.kind, line.table), (None, None, None))

        table = ocr.OcrTable("t1", 3, 4, False)
        cell = OcrLine("7,000.00", 199, 1000, 2280, 1400, None, kind="table_cell", table=table)
        self.assertEqual((cell.kind, cell.table), ("table_cell", table))
        self.assertEqual((table.table_id, table.row, table.col, table.header), ("t1", 3, 4, False))

    def test_unsure_words_and_box_source_are_optional(self) -> None:
        """PRD 5 G3 and G5: an engine that gives neither builds its lines as before."""
        line = OcrLine("Sunil Patil", 192, 718, 432, 781, 0.97)
        self.assertEqual((line.unsure, line.box_source), ((), None))
        marked = OcrLine("Sunil Pat[?]l", 192, 718, 432, 781, None, "paragraph", None, ("Sunil",), "found")
        self.assertEqual((marked.unsure, marked.box_source), (("Sunil",), "found"))


if __name__ == "__main__":
    unittest.main()
