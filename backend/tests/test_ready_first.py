"""Ready first and Dynamic by default (Tushar, 16 Oct 2026: "upload and within 20 sec it
reads and understands the data of 10 pages").

* Ready first: the document is saved and ready with its rule values (checked and labelled,
  so a Verified rule value stays Verified) while the card call is still out; the card, its
  values, the links and the context headers come in a second save, fenced like the first
  (a newer run wins), through the save that writes each person's word change again; the
  model down leaves the rule values, ready; the run's times say both parts.
* Dynamic by default: the first read thinks low, Careful high, and with one allowed model
  Careful is that model thinking harder (its early read is no duplicate).
* The API's ``understanding``: a ready document whose card is still coming.
"""

import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

from kaagaz.ai import usage
from kaagaz.ai.client import ModelUnavailable
from kaagaz.ai.openrouter import PageReply
from kaagaz.api import views
from kaagaz.extraction.values import CARD_RECORD, ExtractedValue
from kaagaz.pipeline import DocumentContent
from kaagaz.reading.ocr import DocumentPages
from kaagaz.reading.spans import BoxLocation
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakeChat, MemoryDocuments, MemoryPipelineStore
from tests.test_dynamic_reading import STANDARD, ModelClient, engine
from tests.test_fast_scan import CLEAN, UNCLEAR
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, page
from tests.test_pipeline_card import CARD, T0, Rig, rig
from tests.test_pipeline_card import CUSTOMER as CARD_CUSTOMER

VERIFIED_RULES = [
    ("document", "invoice_number", "verified", "readers_agree"),
    ("document", "invoice_date", "verified", "readers_agree"),
    ("document", "total_amount", "verified", "readers_agree"),
]


class HookedChat(FakeChat):
    """A FakeChat that runs ``hook`` when the card is asked, before it answers."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        super().__init__(*replies)
        self.hook: Callable[[], None] = _nothing

    def chat_json(self, **request: Any) -> dict[str, Any]:
        self.hook()
        return super().chat_json(**request)


def _nothing() -> None:
    return None


def _labels(content: DocumentContent | None) -> list[tuple[str, str, str, str]]:
    assert content is not None
    return [(v.record_key, v.field, v.label, v.label_reason) for v in content.values]


class ReadyFirstTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chat = HookedChat(CARD)
        self.system: Rig = rig(self.chat)
        self.seen: list[tuple[DocumentStatus | None, DocumentContent | None]] = []

    def _see(self) -> None:
        job_id = next(iter(self.system.runs.rows.values())).document_id
        self.seen.append(
            (
                self.system.statuses.get_status(CARD_CUSTOMER, job_id),
                self.system.store.content(CARD_CUSTOMER, job_id),
            )
        )

    def test_ready_with_the_rule_values_while_the_card_is_asked_then_the_card_after(self) -> None:
        self.chat.hook = self._see
        job = self.system.read()
        [(status, early)] = self.seen
        self.assertIs(status, DocumentStatus.READY)  # the card call is still out
        assert early is not None
        self.assertIsNone(early.facts.card)
        self.assertEqual(_labels(early), VERIFIED_RULES)  # checked: Verified holds before the card
        for value in early.values:  # every value has its page and spot
            self.assertIsInstance(value.location, BoxLocation)
        self.assertNotIn("Type:", early.pieces[0].context_header)

        final = self.system.content(job)
        self.assertEqual(final.facts.doc_type, "receipt")
        self.assertIn("Type: receipt | Date: 2026-08-12", final.pieces[0].context_header)
        self.assertEqual(_labels(final)[:3], VERIFIED_RULES)
        card = [v for v in final.values if v.record_key == CARD_RECORD]
        self.assertTrue(card)
        self.assertTrue(all(v.label == "needs_review" and isinstance(v.location, BoxLocation) for v in card))
        self.assertTrue(final.links)
        self.assertEqual(self.system.store.saves, 2)
        (run,) = self.system.runs.for_document(CARD_CUSTOMER, job.document_id)
        self.assertEqual(run.status, "ready")
        # The ready before the card kept the run open; the last one finished it.
        run_id = next(iter(self.system.runs.rows))
        self.assertEqual(_readied(self.system), [(run_id, True), (run_id, False)])
        marker = [p for _, _, p in self.system.progress.history if p.ready_at is not None]
        self.assertEqual([(p.step, p.ready_at) for p in marker], [("understanding", T0)])

    def test_a_newer_run_started_while_the_card_is_asked_wins(self) -> None:
        def newer() -> None:
            self._see()
            job_id = next(iter(self.system.runs.rows.values())).document_id
            self.system.runs.start(CARD_CUSTOMER, job_id, now=T0)  # a read again's delivery

        self.chat.hook = newer
        job = self.system.read()  # acked: the newer run does the work
        mine, _newer = self.system.runs.for_document(CARD_CUSTOMER, job.document_id)
        self.assertEqual(mine.status, "superseded")
        content = self.system.content(job)
        self.assertIsNone(content.facts.card)  # the card was never saved over the newer run
        self.assertEqual(self.system.store.saves, 1)
        self.assertEqual(len(_readied(self.system)), 1)  # ready before the card only

    def test_the_model_down_after_ready_leaves_the_rule_values_ready(self) -> None:
        down = HookedChat(ModelUnavailable("server_error"))
        system = rig(down)
        statuses: list[DocumentStatus | None] = []
        down.hook = lambda: statuses.append(
            system.statuses.get_status(CARD_CUSTOMER, next(iter(system.runs.rows.values())).document_id)
        )
        job = system.read()
        self.assertEqual(statuses, [DocumentStatus.READY])
        self.assertEqual(system.statuses.get_status(CARD_CUSTOMER, job.document_id), DocumentStatus.READY)
        self.assertEqual(system.content(job), rig().read_content())  # as with no model at all
        totals = system.totals(job)
        self.assertEqual((totals.status, totals.error_code), ("ready", "model_server_error"))

    def test_a_word_change_made_while_the_card_is_asked_is_written_again(self) -> None:
        """The second save is the whole save, the one that writes each word change again
        (PgPipelineStore.save calls word_review.reapply; tests/test_word_review_db.py)."""

        changes: dict[str, str] = {}

        class Reapplying(MemoryPipelineStore):
            def save(self, content: DocumentContent, *, run_id: str | None = None) -> None:
                values = [_changed(value, changes) for value in content.values]
                super().save(replace(content, values=values), run_id=run_id)

        store = Reapplying(self.system.runs)
        self.system.processor._store = store
        self.system.store = store
        self.chat.hook = lambda: changes.update({"INV 17": "INV-17"})  # a person's edit
        job = self.system.read()
        number = next(v for v in self.system.content(job).values if v.field == "invoice_number")
        self.assertEqual(number.value_text, "INV-17")

    def test_the_run_times_its_card_and_both_parts(self) -> None:
        clock = [0.0]

        def card_takes_5_seconds() -> None:
            clock[0] += 5.0

        self.system.processor._timer = lambda: clock[0]
        self.chat.hook = card_takes_5_seconds
        job = self.system.read()
        config = self.system.totals(job).config
        self.assertEqual(
            {key: config[key] for key in ("seconds_card", "seconds_to_ready", "seconds_after_ready")},
            {"seconds_card": 5.0, "seconds_to_ready": 0.0, "seconds_after_ready": 5.0},
        )
        self.assertEqual(config["seconds_after_reading"], 0.0)  # to Ready, as the time left counts


def _readied(system: Rig) -> list[tuple[str | None, bool]]:
    return cast(MemoryDocuments, system.processor._documents).readied


def _changed(value: ExtractedValue, changes: dict[str, str]) -> ExtractedValue:
    return replace(value, value_text=changes.get(value.value_text, value.value_text))


class _ByLevel(ModelClient):
    """A page client answering by thinking level: unclear at low, clean at high."""

    def __init__(self) -> None:
        super().__init__({})

    def read_page(self, *, png: bytes, thinking_level: str, model: str | None = None, **_: Any) -> PageReply:
        with self._lock:
            self.calls.append((model, thinking_level))
        usage.record_call(str(model), None, 10, 20, 0, 0.0)
        return UNCLEAR if thinking_level == "low" else CLEAN


def _one_model(page_count: int) -> DocumentPages:
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=FakeCache(),
        model=STANDARD,
        provider="google-vertex",
        thinking="low",
        careful=(STANDARD, "google-vertex"),  # no Careful tier: the lead itself
        memo={},
        inline_careful=True,
        page_count=page_count,
        escalated={},
    )


class OneModelDynamicTest(unittest.TestCase):
    def test_careful_is_the_same_model_at_high_after_a_low_first_read(self) -> None:
        for early, pages in ((0, 3), (2, 1)):  # inline after the first read; early, alongside it
            with self.subTest(early=early):
                client = _ByLevel()
                reader = engine(client, careful_early_max_pages=early)
                document = _one_model(pages)
                with reader.document(document):
                    read = reader.read_page(page(), page_no=1)
                self.assertEqual(sorted(client.calls), [(STANDARD, "high"), (STANDARD, "low")])
                self.assertEqual((read.read_by, read.lines[0].text), (STANDARD, "Total 1,180.00"))
                self.assertIn(1, document.escalated or {})


class UnderstandingViewTest(unittest.TestCase):
    def test_a_ready_document_is_understood_until_its_run_clears_the_mark_or_it_goes_stale(self) -> None:
        mark = {"step": "understanding", "done": 0, "total": 2, "ready_at": T0.isoformat()}
        now = T0 + timedelta(seconds=30)
        self.assertTrue(views.understanding({"status": "ready", "progress": mark}, now=now))
        self.assertFalse(views.understanding({"status": "ready", "progress": None}, now=now))
        self.assertFalse(views.understanding({"status": "clean", "progress": mark}, now=now))
        old = {"status": "ready", "progress": {"step": "understanding", "done": 2, "total": 2}}
        self.assertFalse(views.understanding(old, now=now))  # left by a run before Ready first
        stale = now + views.UNDERSTANDING_STALE
        self.assertFalse(views.understanding({"status": "ready", "progress": mark}, now=stale))
